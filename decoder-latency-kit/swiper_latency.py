"""Portable, standard-library-only adapter for SWIPER's volume callback."""

import json
import math
import operator
from bisect import bisect_right
from pathlib import Path


def predict_piecewise_us(knots, volume):
    """Clamp below the measured domain; scale proportionally above it."""
    if volume <= knots[0]["volume"]:
        return knots[0]["latency_us"]
    if volume >= knots[-1]["volume"]:
        return knots[-1]["latency_us"] * volume / knots[-1]["volume"]
    index = bisect_right([point["volume"] for point in knots], volume)
    left, right = knots[index-1:index+1]
    fraction = (volume-left["volume"])/(right["volume"]-left["volume"])
    return left["latency_us"] + fraction*(right["latency_us"]-left["latency_us"])


class LatencyProfile:
    def __init__(self, profile, *, round_us, distance, statistic="median", per_job_overhead_us=0):
        if profile.get("schema_version") != 1:
            raise ValueError("Unsupported latency-profile schema")
        if profile.get("input_unit") != "patch_rounds" or profile.get("output_unit") != "microseconds":
            raise ValueError("Expected patch-round input and microsecond output")
        if profile.get("distance") != distance:
            raise ValueError(f"Profile is for distance {profile.get('distance')}, requested {distance}")
        if not math.isfinite(round_us) or round_us <= 0:
            raise ValueError("round_us must be finite and positive")
        if not math.isfinite(per_job_overhead_us) or per_job_overhead_us < 0:
            raise ValueError("per_job_overhead_us must be finite and nonnegative")
        if statistic not in profile["fits"]:
            raise ValueError(f"Statistic {statistic!r} missing; available: {sorted(profile['fits'])}")
        fit = profile["fits"][statistic]
        self.model_type = profile.get("model", "nonnegative_affine")
        if self.model_type == "monotone_piecewise_linear":
            self.knots = fit["knots"]
            if len(self.knots) < 2:
                raise ValueError("At least two latency knots are required")
            for point in self.knots:
                if any(not math.isfinite(point[k]) or point[k] <= 0 for k in ["volume", "latency_us"]):
                    raise ValueError("Latency knots must be finite and positive")
            if any(a["volume"] >= b["volume"] or a["latency_us"] > b["latency_us"] for a, b in zip(self.knots, self.knots[1:])):
                raise ValueError("Knot volumes must increase and latency must not decrease")
        elif self.model_type in {"nonnegative_affine", "fixed_us + us_per_patch_round * volume"}:
            coefficients = [fit["fixed_us"], fit["us_per_patch_round"]]
            if any(not math.isfinite(x) or x < 0 for x in coefficients):
                raise ValueError("Latency coefficients must be finite and nonnegative")
            self.fixed_us, self.us_per_patch_round = coefficients
        else:
            raise ValueError(f"Unsupported latency model: {self.model_type}")
        self.profile = profile
        self.round_us = round_us
        self.distance = distance
        self.statistic = statistic
        self.per_job_overhead_us = per_job_overhead_us

    def latency_us(self, volume):
        if isinstance(volume, bool):
            raise ValueError("volume must be a positive integer patch-round count")
        try:
            volume = operator.index(volume)
        except TypeError as exc:
            raise ValueError("volume must be a positive integer patch-round count") from exc
        if volume < 1:
            raise ValueError("volume must be positive")
        if self.model_type == "monotone_piecewise_linear":
            result = predict_piecewise_us(self.knots, volume) + self.per_job_overhead_us
        else:
            result = self.fixed_us + self.us_per_patch_round * volume + self.per_job_overhead_us
        if not math.isfinite(result):
            raise ValueError("Predicted latency overflowed")
        return result

    def latency_rounds(self, volume):
        # Positive countdowns are required by the upstream decoder manager.
        return max(1, math.ceil(self.latency_us(volume) / self.round_us))

    def description(self):
        return {"distance": self.distance, "statistic": self.statistic,
                "round_us": self.round_us, "per_job_overhead_us": self.per_job_overhead_us,
                "model": self.model_type,
                "calibration_scope": self.profile.get("calibration_scope"),
                "measured_proxy_volume_range": self.profile.get("measured_proxy_volume_range"),
                "provenance": self.profile.get("provenance")}


def load_latency_fn(path, *, round_us, distance, statistic="median", per_job_overhead_us=0):
    """Return f(volume)->integer rounds, ready for decoding_latency_fn=.

    Geometry has been collapsed by the fit. This does not simulate syndrome
    decoding or measure latency on hardware when called by SWIPER.
    """
    model = LatencyProfile(json.loads(Path(path).read_text()), round_us=round_us,
                           distance=distance, statistic=statistic,
                           per_job_overhead_us=per_job_overhead_us)

    def decoding_latency_fn(volume):
        return model.latency_rounds(volume)

    decoding_latency_fn.model = model
    return decoding_latency_fn
