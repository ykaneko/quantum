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
from quantum.tests.unit.ryu import fake_ryu


class TestFakeVMAgentRyu(base.BaseTestCase):

    _AGENT_NAME = 'quantum.fakevm.fakevm_agent_ryu'

    def setUp(self):
        super(TestFakeVMAgentRyu, self).setUp()
        self.addCleanup(mock.patch.stopall)
        self.fake_ryu = fake_ryu.patch_fake_ryu_client().start()
        self.mod_plugin = importutils.import_module(self._AGENT_NAME)
        self.cfg = mock.patch(self._AGENT_NAME + '.cfg').start()
        self.ip_lib = mock.patch(self._AGENT_NAME + '.ip_lib').start()
        self.plugin_base = mock.patch(
            self._AGENT_NAME + '.fakevm_agent_plugin_base').start()
        self.ryu_agent = mock.patch(
            self._AGENT_NAME + '.ryu_quantum_agent').start()
        self.config = mock.patch(self._AGENT_NAME + '.config').start()

    def mock_plugin(self):
        self.conf = mock.Mock()
        self.conf.AGENT.root_helper = 'roothelper'
        self.conf.FAKEVM.allow_multi_node_emulate = False
        self.conf.FAKEVM.host = 'host1'
        self.conf.FAKEVM.vir_bridge = 'brfakevm'
        self.conf.OVS.integration_bridge = 'brint'
        return self.mod_plugin.QuantumFakeVMAgentRyu()

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

        self.conf.FAKEVM.allow_multi_node_emulate = True
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

        self.conf.FAKEVM.allow_multi_node_emulate = True
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

        self.conf.FAKEVM.allow_multi_node_emulate = True
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

        self.conf.FAKEVM.allow_multi_node_emulate = True
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

    def test_init_bridge(self):
        plugin = self.mock_plugin()

        ovs_br = mock.Mock()
        self.conf.FAKEVM.allow_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = False
        with nested(
            mock.patch.object(plugin, '_ensure_bridge'),
            mock.patch.object(plugin, '_ensure_ovs_bridge',
                              return_value=ovs_br),
            mock.patch.object(plugin, '_connect_ovs_lb')
        ) as (mock_ensure_bridge, mock_ensure_ovs_bridge, mock_connect_ovs_lb):
            plugin.init(self.conf)

        mock_ensure_bridge.assert_has_calls([
            mock.call('brfakevm')
        ])
        mock_ensure_ovs_bridge.assert_has_calls([
            mock.call('brint')
        ])
        mock_connect_ovs_lb.assert_has_calls([
            mock.call('qfohost1', 'qfbhost1', ovs_br, 'brfakevm')
        ])

    def test_cleanup_bridge(self):
        plugin = self.mock_plugin()

        ovs_br = mock.Mock()
        self.conf.FAKEVM.allow_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = False
        with nested(
            mock.patch.object(plugin, '_ensure_bridge'),
            mock.patch.object(plugin, '_ensure_ovs_bridge',
                              return_value=ovs_br),
            mock.patch.object(plugin, '_connect_ovs_lb'),
            mock.patch.object(plugin, '_disconnect_ovs_lb')
        ) as (mock_ensure_bridge, mock_ensure_ovs_bridge, mock_connect_ovs_lb,
              mock_disconnect_ovs_lb):
            plugin.init(self.conf)
            plugin._cleanup_bridge()

        mock_disconnect_ovs_lb.assert_has_calls([
            mock.call('qfohost1', 'qfbhost1', ovs_br, 'brfakevm')
        ])

    def test_get_tunnel_name(self):
        plugin = self.mock_plugin()

        with nested(
            mock.patch.object(plugin, '_init_tunnel'),
            mock.patch.object(plugin, '_init_bridge')
        ) as (mock_tunnel, mock_bridge):
            plugin.init(self.conf)
            tunname = plugin._get_tunnel_name()

        self.assertEqual(tunname, 'qfthost1')

    def test_get_tunnel_name_long(self):
        plugin = self.mock_plugin()

        self.conf.FAKEVM.host = '1234567890123'
        with nested(
            mock.patch.object(plugin, '_init_tunnel'),
            mock.patch.object(plugin, '_init_bridge')
        ) as (mock_tunnel, mock_bridge):
            plugin.init(self.conf)
            tunname = plugin._get_tunnel_name()

        self.assertEqual(tunname, 'qft4567890123')

    def test_init_tunnel(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        device = mock.Mock()
        ip_wrapper.add_dummy = mock.Mock(return_value=device)
        self.conf.FAKEVM.allow_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = True
        with nested(
            mock.patch.object(plugin, '_cleanup_tunnel'),
            mock.patch.object(self.ryu_agent, '_get_tunnel_ip',
                              return_value='1.2.3.4'),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              return_value=ip_wrapper),
            mock.patch.object(self.ip_lib, 'device_exists',
                              return_value=False),
            mock.patch.object(plugin, '_ensure_ovs_bridge')
        ) as (mock_cleanup_tunnel, mock_get_tunnel_ip, mock_ipwrapper,
              mock_device_exists, mock_ensure_ovs_bridge):
            plugin.init(self.conf)

        self.assertEqual(mock_cleanup_tunnel.call_count, 1)
        self.assertEqual(mock_get_tunnel_ip.call_count, 1)
        mock_ipwrapper.assert_has_calls([
            mock.call('roothelper')
        ])
        mock_device_exists.assert_has_calls([
            mock.call('qfthost1', 'roothelper')
        ])
        ip_wrapper.assert_has_calls([
            mock.call.add_dummy('qfthost1')
        ])
        device.assert_has_calls([
            mock.call.addr.add(4, '1.2.3.4', '+'),
            mock.call.link.set_up()
        ])
        mock_ensure_ovs_bridge.assert_has_calls([
            mock.call('brint')
        ])

    def test_init_tunnel_exists(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        device = mock.Mock()
        ip_wrapper.device = mock.Mock(return_value=device)
        self.conf.FAKEVM.allow_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = True
        with nested(
            mock.patch.object(plugin, '_cleanup_tunnel'),
            mock.patch.object(self.ryu_agent, '_get_tunnel_ip',
                              return_value='1.2.3.4'),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              return_value=ip_wrapper),
            mock.patch.object(self.ip_lib, 'device_exists',
                              return_value=True),
            mock.patch.object(plugin, '_ensure_ovs_bridge')
        ) as (mock_cleanup_tunnel, mock_get_tunnel_ip, mock_ipwrapper,
              mock_device_exists, mock_ensure_ovs_bridge):
            plugin.init(self.conf)

        self.assertEqual(mock_cleanup_tunnel.call_count, 1)
        self.assertEqual(mock_get_tunnel_ip.call_count, 1)
        mock_ipwrapper.assert_has_calls([
            mock.call('roothelper')
        ])
        mock_device_exists.assert_has_calls([
            mock.call('qfthost1', 'roothelper')
        ])
        ip_wrapper.assert_has_calls([
            mock.call.device('qfthost1')
        ])
        device.assert_has_calls([
            mock.call.addr.add(4, '1.2.3.4', '+'),
            mock.call.link.set_up()
        ])
        mock_ensure_ovs_bridge.assert_has_calls([
            mock.call('brint')
        ])

    def test_cleanup_tunnel(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        device = mock.Mock()
        ip_wrapper.device = mock.Mock(return_value=device)
        self.conf.FAKEVM.allow_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = True
        plugin.init(self.conf)

        with nested(
            mock.patch.object(self.ip_lib, 'device_exists',
                              return_value=True),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              return_value=ip_wrapper)
        ) as (mock_device_exists, mock_ipwrapper):
            plugin._cleanup_tunnel()

        mock_device_exists.assert_has_calls([
            mock.call('qfthost1', 'roothelper')
        ])
        mock_ipwrapper.assert_has_calls([
            mock.call('roothelper')
        ])
        ip_wrapper.assert_has_calls([
            mock.call.device('qfthost1')
        ])
        device.assert_has_calls([
            mock.call.link.delete()
        ])

    def test_cleanup_tunnel_not_exists(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        device = mock.Mock()
        ip_wrapper.device = mock.Mock(return_value=device)
        self.conf.FAKEVM.allow_multi_node_emulate = True
        self.conf.FAKEVM.use_tunnel = True
        plugin.init(self.conf)

        with nested(
            mock.patch.object(self.ip_lib, 'device_exists',
                              return_value=False),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              return_value=ip_wrapper)
        ) as (mock_device_exists, mock_ipwrapper):
            plugin._cleanup_tunnel()

        mock_device_exists.assert_has_calls([
            mock.call('qfthost1', 'roothelper')
        ])
        self.assertEqual(mock_ipwrapper.call_count, 0)
        self.assertEqual(ip_wrapper.device.call_count, 0)
        self.assertEqual(device.link.delete.call_count, 0)
