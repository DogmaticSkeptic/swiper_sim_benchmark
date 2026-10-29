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
            simulator_module.DeviceManager = InventoryDeviceManager
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

    def step_experiment(self, *args, **kwargs):
        super().step_experiment(*args, **kwargs)
        pending = len(self._window_manager.all_windows) - self._decoding_manager._num_completed_windows
        self.peak_pending_windows = max(getattr(self, 'peak_pending_windows', 0), pending)
