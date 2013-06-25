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

from contextlib import nested

import mock

from quantum.openstack.common import importutils
from quantum.tests import base


class TestFakeVMAgentOVS(base.BaseTestCase):

    _AGENT_NAME = 'quantum.debug.fakevm.fakevm_agent_ovs'

    def setUp(self):
        super(TestFakeVMAgentOVS, self).setUp()
        self.addCleanup(mock.patch.stopall)
        self.mod_plugin = importutils.import_module(self._AGENT_NAME)
        self.cfg = mock.patch(self._AGENT_NAME + '.cfg').start()
        self.ip_lib = mock.patch(self._AGENT_NAME + '.ip_lib').start()
        self.ovs_lib = mock.patch(self._AGENT_NAME + '.ovs_lib').start()
        self.q_utils = mock.patch(self._AGENT_NAME + '.q_utils').start()
        self.plugin_base = mock.patch(
            self._AGENT_NAME + '.fakevm_agent_plugin_base').start()
        self.config = mock.patch(self._AGENT_NAME + '.config').start()

    def mock_plugin(self):
        self.conf = mock.Mock()
        self.conf.AGENT.root_helper = 'roothelper'
        self.conf.FAKEVM.enable_multi_node_emulate = False
        self.conf.FAKEVM.host = 'host1'
        self.conf.FAKEVM.vir_bridge = 'brfakevm'
        self.conf.FAKEVM.tunnel_interface = 'tunfv'
        self.conf.OVS.integration_bridge = 'brint'
        return self.mod_plugin.QuantumFakeVMAgentOVS()

    def test_init(self):
        plugin = self.mock_plugin()

        with nested(
            mock.patch.object(plugin, '_init_tunnel'),
            mock.patch.object(plugin, '_init_bridge')
        ) as (mock_tunnel, mock_bridge):
            plugin.init(self.conf)

        self.assertEqual(mock_tunnel.call_count, 0)
        self.assertEqual(mock_bridge.call_count, 0)

    def test_init_to_tunnel(self):
        plugin = self.mock_plugin()

        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = True
        with nested(
            mock.patch.object(plugin, '_init_tunnel'),
            mock.patch.object(plugin, '_init_bridge')
        ) as (mock_tunnel, mock_bridge):
            plugin.init(self.conf)

        self.assertEqual(mock_tunnel.call_count, 1)
        self.assertEqual(mock_bridge.call_count, 0)

    def test_init_to_bridge(self):
        plugin = self.mock_plugin()

        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = False
        with nested(
            mock.patch.object(plugin, '_init_tunnel'),
            mock.patch.object(plugin, '_init_bridge')
        ) as (mock_tunnel, mock_bridge):
            plugin.init(self.conf)

        self.assertEqual(mock_tunnel.call_count, 0)
        self.assertEqual(mock_bridge.call_count, 1)

    def test_cleanup(self):
        plugin = self.mock_plugin()

        with nested(
            mock.patch.object(plugin, '_init_tunnel'),
            mock.patch.object(plugin, '_init_bridge'),
            mock.patch.object(plugin, '_cleanup_tunnel'),
            mock.patch.object(plugin, '_cleanup_bridge')
        ) as (mock_init_tunnel, mock_init_bridge,
              mock_cleanup_tunnel, mock_cleanup_bridge):
            plugin.init(self.conf)
            plugin.cleanup()

        self.assertEqual(mock_cleanup_tunnel.call_count, 0)
        self.assertEqual(mock_cleanup_bridge.call_count, 0)

    def test_cleanup_to_tunnel(self):
        plugin = self.mock_plugin()

        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = True
        with nested(
            mock.patch.object(plugin, '_init_tunnel'),
            mock.patch.object(plugin, '_init_bridge'),
            mock.patch.object(plugin, '_cleanup_tunnel'),
            mock.patch.object(plugin, '_cleanup_bridge')
        ) as (mock_init_tunnel, mock_init_bridge,
              mock_cleanup_tunnel, mock_cleanup_bridge):
            plugin.init(self.conf)
            plugin.cleanup()

        self.assertEqual(mock_cleanup_tunnel.call_count, 1)
        self.assertEqual(mock_cleanup_bridge.call_count, 0)

    def test_cleanup_to_bridge(self):
        plugin = self.mock_plugin()

        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = False
        with nested(
            mock.patch.object(plugin, '_init_tunnel'),
            mock.patch.object(plugin, '_init_bridge'),
            mock.patch.object(plugin, '_cleanup_tunnel'),
            mock.patch.object(plugin, '_cleanup_bridge')
        ) as (mock_init_tunnel, mock_init_bridge,
              mock_cleanup_tunnel, mock_cleanup_bridge):
            plugin.init(self.conf)
            plugin.cleanup()

        self.assertEqual(mock_cleanup_tunnel.call_count, 0)
        self.assertEqual(mock_cleanup_bridge.call_count, 1)

    def test_get_vif_bridge_name(self):
        plugin = self.mock_plugin()

        self.conf.OVS.integration_bridge = 'brint'
        with nested(
            mock.patch.object(plugin, '_init_tunnel'),
            mock.patch.object(plugin, '_init_bridge')
        ) as (mock_tunnel, mock_bridge):
            plugin.init(self.conf)
            brname = plugin._get_vif_bridge_name('fake_net', 'fake_port')

        self.assertEqual(brname, 'brint')

    def test_init_physical_bridge(self):
        plugin = self.mock_plugin()

        plugin.init(self.conf)
        with nested(
            mock.patch.object(plugin, '_ensure_ovs_bridge'),
            mock.patch.object(plugin, '_ensure_bridge'),
            mock.patch.object(plugin, '_connect_ovs_lb')
        ) as (mock_ensure_ovs_bridge, mock_ensure_bridge, mock_connect_ovs_lb):
            plugin._init_physical_bridge()

        self.assertEqual(mock_ensure_ovs_bridge.call_count, 0)
        self.assertEqual(mock_ensure_bridge.call_count, 0)
        self.assertEqual(mock_connect_ovs_lb.call_count, 0)

    def test_init_physical_bridge_one_net(self):
        plugin = self.mock_plugin()

        phy_br = mock.Mock()
        with nested(
            mock.patch.object(self.q_utils, 'parse_mappings',
                              return_value={'fake_physical': 'fake_bridge'}),
            mock.patch.object(plugin, '_ensure_ovs_bridge',
                              return_value=phy_br),
            mock.patch.object(plugin, '_ensure_bridge'),
            mock.patch.object(plugin, '_connect_ovs_lb')
        ) as (mock_parse_mappings, mock_ensure_ovs_bridge, mock_ensure_bridge,
              mock_connect_ovs_lb):
            plugin.init(self.conf)
            plugin._init_physical_bridge()

        mock_ensure_ovs_bridge.assert_has_calls([
            mock.call('fake_bridge')
        ])
        mock_ensure_bridge.assert_has_calls([
            mock.call('bfv-fake_phys')
        ])
        mock_connect_ovs_lb.assert_has_calls([
            mock.call('qfofake_bridg', 'qfbfake_bridg', phy_br,
                      'bfv-fake_phys')
        ])

    def test_init_physical_bridge_two_net(self):
        plugin = self.mock_plugin()

        phy_br = mock.Mock()
        with nested(
            mock.patch.object(self.q_utils, 'parse_mappings',
                              return_value={'fake_phy1': 'fake_br1',
                                            'fake_phy2': 'fake_br2'}),
            mock.patch.object(plugin, '_ensure_ovs_bridge',
                              return_value=phy_br),
            mock.patch.object(plugin, '_ensure_bridge'),
            mock.patch.object(plugin, '_connect_ovs_lb')
        ) as (mock_parse_mappings, mock_ensure_ovs_bridge, mock_ensure_bridge,
              mock_connect_ovs_lb):
            plugin.init(self.conf)
            plugin._init_physical_bridge()

        mock_ensure_ovs_bridge.assert_has_calls([
            mock.call('fake_br1'),
            mock.call('fake_br2'),
        ])
        mock_ensure_bridge.assert_has_calls([
            mock.call('bfv-fake_phy1'),
            mock.call('bfv-fake_phy2')
        ])
        mock_connect_ovs_lb.assert_has_calls([
            mock.call('qfofake_br1', 'qfbfake_br1', phy_br, 'bfv-fake_phy1'),
            mock.call('qfofake_br2', 'qfbfake_br2', phy_br, 'bfv-fake_phy2')
        ])

    def test_cleanup_physical_bridge(self):
        plugin = self.mock_plugin()

        plugin.init(self.conf)
        with nested(
            mock.patch.object(self.ovs_lib, 'OVSBridge'),
            mock.patch.object(plugin, '_disconnect_ovs_lb')
        ) as (mock_ovs_bridge, mock_disconnect_ovs_lb):
            plugin._cleanup_physical_bridge()

        self.assertEqual(mock_ovs_bridge.call_count, 0)
        self.assertEqual(mock_disconnect_ovs_lb.call_count, 0)

    def test_cleanup_physical_bridge_one_net(self):
        plugin = self.mock_plugin()

        phy_br = mock.Mock()
        with nested(
            mock.patch.object(self.q_utils, 'parse_mappings',
                              return_value={'fake_physical': 'fake_bridge'}),
            mock.patch.object(self.ovs_lib, 'OVSBridge', return_value=phy_br),
            mock.patch.object(plugin, '_disconnect_ovs_lb')
        ) as (mock_parse_mappings, mock_ovs_bridge, mock_disconnect_ovs_lb):
            plugin.init(self.conf)
            plugin._cleanup_physical_bridge()

        mock_ovs_bridge.assert_has_calls([
            mock.call('fake_bridge', 'roothelper')
        ])
        mock_disconnect_ovs_lb.assert_has_calls([
            mock.call('qfofake_bridg', 'qfbfake_bridg', phy_br,
                      'bfv-fake_phys')
        ])

    def test_cleanup_physical_bridge_two_net(self):
        plugin = self.mock_plugin()

        phy_br = mock.Mock()
        with nested(
            mock.patch.object(self.q_utils, 'parse_mappings',
                              return_value={'fake_phy1': 'fake_br1',
                                            'fake_phy2': 'fake_br2'}),
            mock.patch.object(self.ovs_lib, 'OVSBridge', return_value=phy_br),
            mock.patch.object(plugin, '_disconnect_ovs_lb')
        ) as (mock_parse_mappings, mock_ovs_bridge, mock_disconnect_ovs_lb):
            plugin.init(self.conf)
            plugin._cleanup_physical_bridge()

        mock_ovs_bridge.assert_has_calls([
            mock.call('fake_br1', 'roothelper'),
            mock.call('fake_br2', 'roothelper'),
        ])
        mock_disconnect_ovs_lb.assert_has_calls([
            mock.call('qfofake_br1', 'qfbfake_br1', phy_br, 'bfv-fake_phy1'),
            mock.call('qfofake_br2', 'qfbfake_br2', phy_br, 'bfv-fake_phy2')
        ])

    def test_init_bridge(self):
        plugin = self.mock_plugin()

        ovs_br = mock.Mock()
        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = False
        with nested(
            mock.patch.object(self.q_utils, 'parse_mappings',
                              return_value=None),
            mock.patch.object(plugin, '_ensure_ovs_bridge',
                              return_value=ovs_br),
            mock.patch.object(plugin, '_init_physical_bridge'),
            mock.patch.object(plugin, '_ensure_bridge'),
            mock.patch.object(plugin, '_connect_ovs_lb')
        ) as (mock_parse_mappings, mock_ensure_ovs_bridge,
              mock_init_physical_bridge, mock_ensure_bridge,
              mock_connect_ovs_lb):
            plugin.init(self.conf)

        mock_ensure_ovs_bridge.assert_has_calls([
            mock.call('brint')
        ])
        self.assertEqual(mock_init_physical_bridge.call_count, 0)
        mock_ensure_bridge.assert_has_calls([
            mock.call('brfakevm')
        ])
        mock_connect_ovs_lb.assert_has_calls([
            mock.call('qfohost1', 'qfbhost1', ovs_br, 'brfakevm')
        ])

    def test_init_bridge_with_mapping(self):
        plugin = self.mock_plugin()

        ovs_br = mock.Mock()
        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = False
        with nested(
            mock.patch.object(self.q_utils, 'parse_mappings',
                              return_value={'fake_physical': 'fake_bridge'}),
            mock.patch.object(plugin, '_ensure_ovs_bridge',
                              return_value=ovs_br),
            mock.patch.object(plugin, '_init_physical_bridge'),
            mock.patch.object(plugin, '_ensure_bridge'),
            mock.patch.object(plugin, '_connect_ovs_lb')
        ) as (mock_parse_mappings, mock_ensure_ovs_bridge,
              mock_init_physical_bridge, mock_ensure_bridge,
              mock_connect_ovs_lb):
            plugin.init(self.conf)

        mock_ensure_ovs_bridge.assert_has_calls([
            mock.call('brint')
        ])
        self.assertEqual(mock_init_physical_bridge.call_count, 1)
        self.assertEqual(mock_ensure_bridge.call_count, 0)
        self.assertEqual(mock_connect_ovs_lb.call_count, 0)

    def test_cleanup_bridge(self):
        plugin = self.mock_plugin()

        ovs_br = mock.Mock()
        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = False
        with nested(
            mock.patch.object(self.q_utils, 'parse_mappings',
                              return_value=None),
            mock.patch.object(plugin, '_ensure_bridge'),
            mock.patch.object(plugin, '_connect_ovs_lb'),
            mock.patch.object(plugin, '_ensure_ovs_bridge',
                              return_value=ovs_br),
            mock.patch.object(plugin, '_init_physical_bridge'),
            mock.patch.object(plugin, '_cleanup_physical_bridge'),
            mock.patch.object(plugin, '_disconnect_ovs_lb')
        ) as (mock_parse_mappings, mock_ensure_ovs_bridge,
              mock_ensure_bridge, mock_connect_ovs_lb,
              mock_init_physical_bridge, mock_cleanup_physical_bridge,
              mock_disconnect_ovs_lb):
            plugin.init(self.conf)
            plugin._cleanup_bridge()

        self.assertEqual(mock_cleanup_physical_bridge.call_count, 0)
        mock_disconnect_ovs_lb.assert_has_calls([
            mock.call('qfohost1', 'qfbhost1', ovs_br, 'brfakevm')
        ])

    def test_cleanup_bridge_with_mapping(self):
        plugin = self.mock_plugin()

        ovs_br = mock.Mock()
        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = False
        with nested(
            mock.patch.object(self.q_utils, 'parse_mappings',
                              return_value={'fake_physical': 'fake_bridge'}),
            mock.patch.object(plugin, '_ensure_ovs_bridge',
                              return_value=ovs_br),
            mock.patch.object(plugin, '_init_physical_bridge'),
            mock.patch.object(plugin, '_cleanup_physical_bridge'),
            mock.patch.object(plugin, '_disconnect_ovs_lb')
        ) as (mock_parse_mappings, mock_ensure_ovs_bridge,
              mock_init_physical_bridge, mock_cleanup_physical_bridge,
              mock_disconnect_ovs_lb):
            plugin.init(self.conf)
            plugin._cleanup_bridge()

        self.assertEqual(mock_cleanup_physical_bridge.call_count, 1)
        self.assertEqual(mock_disconnect_ovs_lb.call_count, 0)

    def test_init_tunnel(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        device = mock.Mock()
        ip_wrapper.add_dummy = mock.Mock(return_value=device)
        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = True
        self.conf.OVS.local_ip = '1.2.3.4'
        with nested(
            mock.patch.object(plugin, '_cleanup_tunnel'),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              return_value=ip_wrapper),
            mock.patch.object(self.ip_lib, 'device_exists',
                              return_value=False)
        ) as (mock_cleanup_tunnel, mock_ipwrapper, mock_device_exists):
            plugin.init(self.conf)

        self.assertEqual(mock_cleanup_tunnel.call_count, 1)
        mock_ipwrapper.assert_has_calls([
            mock.call('roothelper')
        ])
        mock_device_exists.assert_has_calls([
            mock.call('tunfv', 'roothelper')
        ])
        ip_wrapper.assert_has_calls([
            mock.call.add_dummy('tunfv')
        ])
        device.assert_has_calls([
            mock.call.addr.add(4, '1.2.3.4', '+'),
            mock.call.link.set_up()
        ])

    def test_init_tunnel_exists(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        device = mock.Mock()
        ip_wrapper.device = mock.Mock(return_value=device)
        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = True
        self.conf.OVS.local_ip = '1.2.3.4'
        with nested(
            mock.patch.object(plugin, '_cleanup_tunnel'),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              return_value=ip_wrapper),
            mock.patch.object(self.ip_lib, 'device_exists',
                              return_value=True)
        ) as (mock_cleanup_tunnel, mock_ipwrapper, mock_device_exists):
            plugin.init(self.conf)

        self.assertEqual(mock_cleanup_tunnel.call_count, 1)
        mock_ipwrapper.assert_has_calls([
            mock.call('roothelper')
        ])
        mock_device_exists.assert_has_calls([
            mock.call('tunfv', 'roothelper')
        ])
        ip_wrapper.assert_has_calls([
            mock.call.device('tunfv')
        ])
        device.assert_has_calls([
            mock.call.addr.add(4, '1.2.3.4', '+'),
            mock.call.link.set_up()
        ])

    def test_init_tunnel_no_devname(self):
        plugin = self.mock_plugin()

        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = True
        self.conf.FAKEVM.tunnel_interface = None
        with nested(
            mock.patch.object(plugin, '_cleanup_tunnel'),
            mock.patch.object(self.ip_lib, 'IPWrapper'),
            mock.patch.object(self.ip_lib, 'device_exists'),
        ) as (mock_cleanup_tunnel, mock_ipwrapper, mock_device_exists):
            self.assertRaises(RuntimeError, plugin.init, self.conf)

        self.assertEqual(mock_cleanup_tunnel.call_count, 1)
        self.assertEqual(mock_ipwrapper.call_count, 0)
        self.assertEqual(mock_device_exists.call_count, 0)

    def test_init_tunnel_no_localip(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        device = mock.Mock()
        ip_wrapper.add_dummy = mock.Mock(return_value=device)
        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = True
        self.conf.OVS.local_ip = None
        with nested(
            mock.patch.object(plugin, '_cleanup_tunnel'),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              return_value=ip_wrapper),
            mock.patch.object(self.ip_lib, 'device_exists',
                              return_value=False)
        ) as (mock_cleanup_tunnel, mock_ipwrapper, mock_device_exists):
            plugin.init(self.conf)

        self.assertEqual(mock_cleanup_tunnel.call_count, 1)
        mock_ipwrapper.assert_has_calls([
            mock.call('roothelper')
        ])
        mock_device_exists.assert_has_calls([
            mock.call('tunfv', 'roothelper')
        ])
        ip_wrapper.assert_has_calls([
            mock.call.add_dummy('tunfv')
        ])
        self.assertEqual(device.addr.add.call_count, 0)
        self.assertEqual(device.link.set_up.call_count, 0)

    def test_cleanup_tunnel(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        device = mock.Mock()
        ip_wrapper.device = mock.Mock(return_value=device)
        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = True
        with nested(
            mock.patch.object(plugin, '_init_tunnel'),
            mock.patch.object(self.ip_lib, 'device_exists',
                              return_value=True),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              return_value=ip_wrapper)
        ) as (mock_init_tunnel, mock_device_exists, mock_ipwrapper):
            plugin.init(self.conf)
            plugin._cleanup_tunnel()

        mock_device_exists.assert_has_calls([
            mock.call('tunfv', 'roothelper')
        ])
        mock_ipwrapper.assert_has_calls([
            mock.call('roothelper')
        ])
        ip_wrapper.assert_has_calls([
            mock.call.device('tunfv')
        ])
        device.assert_has_calls([
            mock.call.link.delete()
        ])

    def test_cleanup_tunnel_not_exists(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        device = mock.Mock()
        ip_wrapper.device = mock.Mock(return_value=device)
        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = True
        with nested(
            mock.patch.object(plugin, '_init_tunnel'),
            mock.patch.object(self.ip_lib, 'device_exists',
                              return_value=False),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              return_value=ip_wrapper)
        ) as (mock_init_tunnel, mock_device_exists, mock_ipwrapper):
            plugin.init(self.conf)
            plugin._cleanup_tunnel()

        mock_device_exists.assert_has_calls([
            mock.call('tunfv', 'roothelper')
        ])
        self.assertEqual(mock_ipwrapper.call_count, 0)
        self.assertEqual(ip_wrapper.device.call_count, 0)
        self.assertEqual(device.link.delete.call_count, 0)

    def test_cleanup_tunnel_no_devname(self):
        plugin = self.mock_plugin()

        self.conf.FAKEVM.enable_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = True
        self.conf.FAKEVM.tunnel_interface = None
        with nested(
            mock.patch.object(plugin, '_init_tunnel'),
            mock.patch.object(self.ip_lib, 'device_exists'),
            mock.patch.object(self.ip_lib, 'IPWrapper'),
        ) as (mock_init_tunnel, mock_device_exists, mock_ipwrapper):
            plugin.init(self.conf)
            self.assertRaises(RuntimeError, plugin._cleanup_tunnel)

        self.assertEqual(mock_device_exists.call_count, 0)
        self.assertEqual(mock_ipwrapper.call_count, 0)
