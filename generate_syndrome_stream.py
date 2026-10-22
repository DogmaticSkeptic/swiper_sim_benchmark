"""Export paired raw measurements, detector events, and logical-observable flips."""

import argparse
import json
from pathlib import Path

import stim


def packet_layout(circuit, rounds, round_us, readout_us):
    """Map detector availability using its actual measurement dependencies."""
    packets = []
    measurement_packet = []
    detector_index = 0
    reset_measurements = {"MR", "MRX", "MRY"}
    final_measurements = {"M", "MX", "MY"}
    for instruction in circuit.flattened():
        if instruction.name in reset_measurements | final_measurements:
            final = instruction.name in final_measurements
            packet_index = len(packets)
            start = len(measurement_packet)
            targets = instruction.targets_copy()
            measurement_packet.extend([packet_index] * len(targets))
            packets.append({
                "packet_index": packet_index,
                "kind": "final_data_readout" if final else "syndrome_round",
                "release_time_us": rounds * round_us + readout_us if final
                                   else (packet_index + 1) * round_us,
                "measurement_range": [start, len(measurement_packet)],
                "measured_qubits": [target.value for target in targets],
                "detector_indices": [],
            })
        elif instruction.name == "DETECTOR":
            dependencies = [
                len(measurement_packet) + target.value
                for target in instruction.targets_copy()
                if target.is_measurement_record_target
            ]
            if not dependencies or min(dependencies) < 0:
                raise ValueError("Unsupported detector measurement dependencies")
            available_packet = max(measurement_packet[index] for index in dependencies)
            packets[available_packet]["detector_indices"].append(detector_index)
            detector_index += 1
    if (len(packets) != rounds + 1
            or len(measurement_packet) != circuit.num_measurements
            or detector_index != circuit.num_detectors
            or packets[-1]["kind"] != "final_data_readout"):
        raise ValueError("Unexpected generated memory-circuit measurement layout")
    return packets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True,
                        help="New output directory; existing directories are never overwritten")
    parser.add_argument("--distance", type=int, default=7)
    parser.add_argument("--rounds", type=int, default=None, help="Defaults to 2 * distance")
    parser.add_argument("--shots", type=int, default=1000,
                        help="Independent experiments; rounds within a shot share error history")
    parser.add_argument("--basis", choices=["x", "z"], default="z")
    parser.add_argument("--p", type=float, default=0.001,
                        help="Noise probability for each of the four listed noise mechanisms")
    parser.add_argument("--round-us", type=float, default=1.0)
    parser.add_argument("--readout-us", type=float, default=1.0,
                        help="Nominal final-data readout duration, separate from QEC rounds")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    rounds = args.rounds if args.rounds is not None else 2 * args.distance
    if args.distance < 3 or args.distance % 2 == 0:
        parser.error("distance must be odd and at least 3")
    if rounds < 1 or args.shots < 1:
        parser.error("rounds and shots must be positive")
    if not 0 <= args.p <= 1 or args.round_us <= 0 or args.readout_us < 0:
        parser.error("p must be in [0, 1], round-us positive, and readout-us nonnegative")
    if not 0 <= args.seed < 2**64:
        parser.error("seed must be an unsigned 64-bit integer")
    if args.out.exists():
        parser.error(f"output directory already exists: {args.out}")

    noise = {
        "after_clifford_depolarization": args.p,
        "before_round_data_depolarization": args.p,
        "before_measure_flip_probability": args.p,
        "after_reset_flip_probability": args.p,
    }
    circuit = stim.Circuit.generated(
        f"surface_code:rotated_memory_{args.basis}",
        distance=args.distance, rounds=rounds, **noise,
    )
    dem = circuit.detector_error_model(decompose_errors=True)
    packets = packet_layout(circuit, rounds, args.round_us, args.readout_us)
    args.out.mkdir(parents=True)
    (args.out / "circuit.stim").write_text(str(circuit) + "\n")
    (args.out / "decoder.dem").write_text(str(dem) + "\n")

    # Sample once, then derive detector events and observables from those same
    # raw measurements. Independent samplers would not produce paired records.
    circuit.compile_sampler(seed=args.seed).sample_write(
        shots=args.shots, filepath=str(args.out / "measurements.b8"), format="b8",
    )
    circuit.compile_m2d_converter().convert_file(
        measurements_filepath=str(args.out / "measurements.b8"), measurements_format="b8",
        detection_events_filepath=str(args.out / "detections.b8"), detection_events_format="b8",
        obs_out_filepath=str(args.out / "observables.b8"), obs_out_format="b8",
    )
    widths = {
        "measurements": circuit.num_measurements,
        "detections": circuit.num_detectors,
        "observables": circuit.num_observables,
    }
    metadata = {
        "schema_version": 1,
        "generator": "Stim surface-code memory circuit; independent of SWIPER scheduling",
        "stim_version": stim.__version__,
        "distance": args.distance, "rounds": rounds, "shots": args.shots,
        "basis": args.basis, "seed": args.seed, "noise": noise,
        "round_us": args.round_us, "readout_us": args.readout_us,
        "files": {
            f"{name}.b8": {
                "format": "b8", "bits_per_shot": width,
                "bytes_per_shot": (width + 7) // 8,
            } for name, width in widths.items()
        },
        "packing": "Shot-major, little-endian bits, zero-padded to byte boundary per shot",
        "shot_semantics": "Each shot resets the code; do not concatenate shots as one continuous history",
        "timing_semantics": "Nominal per-shot replay times; generation is offline and is not real-time paced",
        "packet_semantics": "Half-open measurement ranges and detector IDs become available at each release time",
        "packets": packets,
        "detector_coordinates": circuit.get_detector_coordinates(),
    }
    (args.out / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps({
        "out": str(args.out), "shots": args.shots, "rounds_per_shot": rounds,
        "bits_per_shot": widths, "packets_per_shot": len(packets),
    }, indent=2))


if __name__ == "__main__":
    main()
