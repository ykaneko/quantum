# Copyright (c) 2013 OpenStack Foundation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import mock

from quantum.openstack.common import importutils
from quantum.tests import base


class TestFakeVMAgentLB(base.BaseTestCase):

    _AGENT_NAME = 'quantum.fakevm.fakevm_agent_lb'

    def setUp(self):
        super(TestFakeVMAgentLB, self).setUp()
        self.addCleanup(mock.patch.stopall)
        self.mod_plugin = importutils.import_module(self._AGENT_NAME)
        self.plugin_base = mock.patch(
            self._AGENT_NAME + '.fakevm_agent_plugin_base').start()
        self.config = mock.patch(self._AGENT_NAME + '.config').start()

    def mock_plugin(self):
        self.conf = mock.Mock()
        self.conf.FAKEVM.allow_multi_node_emulate = False
        self.conf.FAKEVM.host = 'host1'
        return self.mod_plugin.QuantumFakeVMAgentLB()

    def test_init(self):
        plugin = self.mock_plugin()

        plugin.init(self.conf)

    def test_init_multi_node_mode(self):
        plugin = self.mock_plugin()
        self.conf.FAKEVM.allow_multi_node_emulate = True

        self.assertRaises(SystemExit, plugin.init, self.conf)

    def test_cleanup(self):
        plugin = self.mock_plugin()

        plugin.cleanup()

    def test_get_vif_bridge_name(self):
        plugin = self.mock_plugin()

        rc = plugin._get_vif_bridge_name('fake_net', 'fake_port')

        self.assertEqual(rc, 'brqfake_net')

    def test_get_vif_bridge_name_long(self):
        plugin = self.mock_plugin()

        rc = plugin._get_vif_bridge_name('1234567890123', 'fake_port')

        self.assertEqual(rc, 'brq12345678901')

    def test_get_veth_pair_names(self):
        plugin = self.mock_plugin()

        tap, qfv = plugin._get_veth_pair_names('fake_port')

        self.assertEqual(tap, 'tapfake_port')
        self.assertEqual(qfv, 'qfvfake_port')

    def test_get_veth_pair_names_long(self):
        plugin = self.mock_plugin()

        tap, qfv = plugin._get_veth_pair_names('1234567890123')

        self.assertEqual(tap, 'tap12345678901')
        self.assertEqual(qfv, 'qfv1234567890')

    def test_make_vif_args(self):
        plugin = self.mock_plugin()
        plugin.init(self.conf)

        expect_inst = {'host': 'host1', 'uuid': 'i-xxxx'}
        expect_net = {'id': 'fake_net', 'bridge': 'brname',
                      'bridge_interface': None}
        expect_map = {'vif_type': 'bridge', 'vif_uuid': 'fake_port',
                      'mac': 'aa:bb:cc:dd:ee:ff',
                      'should_create_bridge': True}

        instance, vif = plugin._make_vif_args(
            'i-xxxx', 'fake_net', 'fake_port', 'aa:bb:cc:dd:ee:ff', 'brname')

        self.assertEqual(instance, expect_inst)
        self.assertEqual(vif, (expect_net, expect_map))

    def test_make_vif_args_without_brname(self):
        plugin = self.mock_plugin()
        plugin.init(self.conf)

        expect_inst = {'host': 'host1', 'uuid': 'i-xxxx'}
        expect_net = {'id': 'fake_net', 'bridge': 'brqfake_net',
                      'bridge_interface': None}
        expect_map = {'vif_type': 'bridge', 'vif_uuid': 'fake_port',
                      'mac': 'aa:bb:cc:dd:ee:ff',
                      'should_create_bridge': True}

        instance, vif = plugin._make_vif_args(
            'i-xxxx', 'fake_net', 'fake_port', 'aa:bb:cc:dd:ee:ff', None)

        self.assertEqual(instance, expect_inst)
        self.assertEqual(vif, (expect_net, expect_map))

    def test_probe_plug(self):
        plugin = self.mock_plugin()
        plugin.init(self.conf)

        with mock.patch.object(plugin, '_create_probe') as mock_create_probe:
            plugin._probe_plug('fake_net', 'fake_port', 'aa:bb:cc:dd:ee:ff')

        mock_create_probe.assert_has_calls([
            mock.call('tapfake_port', 'qfvfake_port', 'aa:bb:cc:dd:ee:ff',
                      None, 'fakevm-host1-fake_port')
        ])

    def test_probe_unplug(self):
        plugin = self.mock_plugin()
        plugin.init(self.conf)

        with mock.patch.object(plugin, '_delete_probe') as mock_delete_probe:
            plugin._probe_unplug('fake_net', 'fake_port')

        mock_delete_probe.assert_has_calls([
            mock.call('tapfake_port', 'qfvfake_port', 'brqfake_net',
                      'fakevm-host1-fake_port')
        ])
