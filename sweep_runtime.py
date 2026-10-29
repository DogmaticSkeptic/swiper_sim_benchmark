"""Scheduling-preserving accelerations already verified by the window inventory.

Use incremental DAG construction with upstream predecessor order, defer exact
future start predictions behind conservative bounds, and reject spatially
unrelated windows before the exact boundary check. Full syndrome traces remain
available at lightweight_setting=0. Each sweep runs in its own process.
"""
import swiper.simulator as simulator_module
from swiper.simulator import DecodingSimulator
from swiper.decoder_manager import DecoderManager
from swiper.window_builder import DecodingWindow
from inventory_windows import (InventoryDeviceManager, InventorySchedule,
                               filtered_shares_boundary)


class SweepDeviceManager(InventoryDeviceManager):
    def _postprocess_idle_data(self, syndrome_data):
        # Upstream performs only membership tests on this collection during
        # formatting. A set preserves the result and avoids scanning all S gates
        # for every syndrome object. Restore the original appendable history.
        locations = self._conditional_S_locations
        try:
            self._conditional_S_locations = set(locations)
            return super()._postprocess_idle_data(syndrome_data)
        finally:
            self._conditional_S_locations = locations


class SweepDecoderManager(DecoderManager):
    def _topologically_sort(self, task_indices):
        if self.speculation_mode is None:
            # Without speculation a child starts only after every parent has
            # completed in an earlier round. Simultaneous completions therefore
            # have no mutual dependencies. Do not walk their pending descendants.
            return task_indices
        return super()._topologically_sort(task_indices)


class SweepSimulator(DecodingSimulator):
    def initialize_experiment(self, **kwargs):
        original = simulator_module.DeviceManager
        original_decoder = simulator_module.DecoderManager
        try:
            simulator_module.DeviceManager = SweepDeviceManager
            simulator_module.DecoderManager = SweepDecoderManager
            super().initialize_experiment(**kwargs)
        finally:
            simulator_module.DeviceManager = original
            simulator_module.DecoderManager = original_decoder
        process_round = self._window_manager.process_round
        def filtered_round(rounds):
            original_boundary = DecodingWindow.shares_boundary
            try:
                DecodingWindow.shares_boundary = filtered_shares_boundary
                return process_round(rounds)
            finally:
                DecodingWindow.shares_boundary = original_boundary
        self._window_manager.process_round = filtered_round

    def pending_window_count(self):
        # all_windows retains None entries for merged-away windows. The live
        # graph excludes those entries but retains completed windows, so this
        # difference counts actual unfinished (including unconstructed) windows.
        return self._window_manager.window_dag.number_of_nodes() - self._decoding_manager._num_completed_windows

    def step_experiment(self, pending_window_count_cutoff=0, device_rounds_cutoff=0, print_interval=None):
        pending = self.pending_window_count()
        self.peak_pending_windows = max(getattr(self, 'peak_pending_windows', 0), pending)
        if pending_window_count_cutoff > 0 and pending > pending_window_count_cutoff:
            self.failed = True
            return
        super().step_experiment(pending_window_count_cutoff=0,
                               device_rounds_cutoff=device_rounds_cutoff,
                               print_interval=print_interval)
        self.peak_pending_windows = max(self.peak_pending_windows, self.pending_window_count())
