import unittest
from unittest.mock import patch
import runtime


class DiscoveryTests(unittest.TestCase):
    def test_missing_runtime_probes_each_address_only_once(self):
        client = runtime.Ollama()
        with patch.object(client, 'up', return_value=False) as probe, patch.object(runtime, 'missing', return_value=['ollama']):
            client.ensure()
        self.assertEqual([c.args[0] for c in probe.call_args_list], [runtime.SYSTEM, runtime.BUNDLED])

    def test_discovering_bundled_server_prepares_its_model_only_once(self):
        client = runtime.Ollama()
        with patch.object(client, 'up', side_effect=lambda url: url == runtime.BUNDLED), patch.object(client, 'import_planner') as prepare:
            self.assertEqual(client.ensure(), runtime.BUNDLED)
            self.assertEqual(client.ensure(), runtime.BUNDLED)
        prepare.assert_called_once()

    def test_offline_queries_share_a_short_retry_window(self):
        client = runtime.Ollama()
        with patch.object(client, 'up', return_value=False) as probe, patch.object(runtime, 'missing', return_value=['ollama']), \
             patch.object(runtime.time, 'monotonic', return_value=10) as clock:
            client.ensure(); client.ensure()
            self.assertEqual(probe.call_count, 2)
            clock.return_value = 14
            client.ensure()
            self.assertEqual(probe.call_count, 4)
