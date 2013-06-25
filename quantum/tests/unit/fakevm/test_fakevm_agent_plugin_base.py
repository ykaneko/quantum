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

from quantum.debug.fakevm import fakevm_agent_plugin_base
from quantum.tests import base


class FakeVMAgentPlugin(fakevm_agent_plugin_base.QuantumFakeVMAgentPluginBase):
    def _get_vif_bridge_name(self, network_id, vif_uuid):
        pass


class TestFakeVMAgentPluginBase(base.BaseTestCase):

    _AGENT_NAME = 'quantum.debug.fakevm.fakevm_agent_plugin_base'

    def setUp(self):
        super(TestFakeVMAgentPluginBase, self).setUp()
        self.addCleanup(mock.patch.stopall)
        self.cfg = mock.patch(self._AGENT_NAME + '.cfg').start()
        self.ip_lib = mock.patch(self._AGENT_NAME + '.ip_lib').start()
        self.ovs_lib = mock.patch(self._AGENT_NAME + '.ovs_lib').start()
        self.logging = mock.patch(self._AGENT_NAME + '.logging').start()

        self.conf = mock.Mock()
        self.conf.FAKEVM.host = 'host1'
        self.conf.FAKEVM.nova_conf = 'nova.conf'
        self.conf.AGENT.root_helper = 'roothelper'

    def mock_plugin(self):
        plugin = FakeVMAgentPlugin()
        plugin.init(self.conf)
        return plugin

    def test_get_veth_pair_names(self):
        plugin = self.mock_plugin()

        br_veth, vm_veth = plugin._get_veth_pair_names('fake_port')

        self.assertEqual(br_veth, 'qfbfake_port')
        self.assertEqual(vm_veth, 'qfvfake_port')

    def test_get_ns_name(self):
        plugin = self.mock_plugin()

        nsname = plugin._get_ns_name('fake_port')

        self.assertEqual(nsname, 'fakevm-host1-fake_port')

    def test_get_probe_br_name(self):
        plugin = self.mock_plugin()

        with nested(
            mock.patch(self._AGENT_NAME + '.utils.execute',
                       return_value='qbr'),
            mock.patch(self._AGENT_NAME + '.os.path.join',
                       return_value='vif.py')
        ) as (mock_exec, mock_join):
            brname = plugin._get_probe_br_name('fake_net', 'fake_port')

        self.assertEqual(brname, 'qbr')
        mock_exec.assert_has_calls([
            mock.call(['python', 'vif.py', '--config-file', 'nova.conf',
                       'bridge-name', 'fake_port'])
        ])

    def test_execute(self):
        plugin = self.mock_plugin()

        with mock.patch(self._AGENT_NAME + '.utils.execute') as mock_exec:
            plugin._execute('command')

        mock_exec.assert_has_calls([
            mock.call('command', root_helper='roothelper')
        ])

    def test_device_exists(self):
        plugin = self.mock_plugin()

        with mock.patch(self._AGENT_NAME + '.utils.execute') as mock_exec:
            rc = plugin._device_exists('fakedevice')

        self.assertEqual(rc, True)
        mock_exec.assert_has_calls([
            mock.call(['ip', 'link', 'show', 'dev', 'fakedevice'],
                      root_helper='roothelper')
        ])

    def test_device_exists_no_device(self):
        plugin = self.mock_plugin()

        with mock.patch(self._AGENT_NAME + '.utils.execute',
                        side_effect=RuntimeError) as mock_exec:
            rc = plugin._device_exists('fakedevice')

        self.assertEqual(rc, False)
        mock_exec.assert_has_calls([
            mock.call(['ip', 'link', 'show', 'dev', 'fakedevice'],
                      root_helper='roothelper')
        ])

    def test_ensure_bridge(self):
        plugin = self.mock_plugin()

        with nested(
            mock.patch(self._AGENT_NAME +
                       '.QuantumFakeVMAgentPluginBase._device_exists',
                       return_value=False),
            mock.patch(self._AGENT_NAME + '.utils.execute'),
        ) as (mock_device_exists, mock_exec):
            plugin._ensure_bridge('fakedevice')

        mock_device_exists.assert_has_calls([
            mock.call('fakedevice')
        ])
        mock_exec.assert_has_calls([
            mock.call(['brctl', 'addbr', 'fakedevice'],
                      root_helper='roothelper'),
            mock.call(['brctl', 'setfd', 'fakedevice', '0'],
                      root_helper='roothelper'),
            mock.call(['brctl', 'stp', 'fakedevice', 'off'],
                      root_helper='roothelper'),
            mock.call(['ip', 'link', 'set', 'fakedevice', 'up'],
                      root_helper='roothelper'),
        ])

    def test_ensure_bridge_exists(self):
        plugin = self.mock_plugin()

        with nested(
            mock.patch(self._AGENT_NAME +
                       '.QuantumFakeVMAgentPluginBase._device_exists',
                       return_value=True),
            mock.patch(self._AGENT_NAME + '.time.sleep'),
        ) as (mock_device_exists, mock_sleep):
            plugin._ensure_bridge('fakedevice')

        mock_device_exists.assert_has_calls([
            mock.call('fakedevice')
        ])
        self.assertEqual(mock_sleep.call_count, 1)

    def test_ensure_ovs_bridge(self):
        plugin = self.mock_plugin()

        rc = plugin._ensure_ovs_bridge('fakeovs')

        self.assertEqual(rc, self.ovs_lib.OVSBridge.return_value)
        self.ovs_lib.OVSBridge.assert_has_calls([
            mock.call('fakeovs', 'roothelper')
        ])
        self.ovs_lib.OVSBridge.return_value.assert_has_calls([
            mock.call.run_vsctl(['--', '--may-exist', 'add-br', 'fakeovs'])
        ])

    def test_connect_ovs_lb(self):
        plugin = self.mock_plugin()

        ovs_br = mock.Mock()
        ovs_veth = mock.Mock()
        br_veth = mock.Mock()

        with nested(
            mock.patch(self._AGENT_NAME + '.utils.execute'),
            mock.patch.object(self.ip_lib.IPWrapper.return_value,
                              'add_veth', return_value=[ovs_veth, br_veth])
        ) as (mock_exec, mock_add_veth):
            plugin._connect_ovs_lb('ovs_veth', 'br_veth', ovs_br, 'brname')

        self.ip_lib.IPWrapper.assert_has_calls([
            mock.call('roothelper')
        ])
        mock_add_veth.assert_has_calls([
            mock.call('ovs_veth', 'br_veth')
        ])
        ovs_br.assert_has_calls([
            mock.call.add_port('ovs_veth')
        ])
        mock_exec.assert_has_calls([
            mock.call(['brctl', 'addif', 'brname', 'br_veth'],
                      root_helper='roothelper')
        ])
        ovs_veth.link.assert_has_calls([
            mock.call.set_up()
        ])
        br_veth.link.assert_has_calls([
            mock.call.set_up()
        ])

    def test_disconnect_ovs_lb(self):
        plugin = self.mock_plugin()

        ovs_br = mock.Mock()
        ovs_veth = mock.Mock()
        br_veth = mock.Mock()

        with nested(
            mock.patch(self._AGENT_NAME + '.utils.execute'),
            mock.patch.object(self.ip_lib.IPWrapper.return_value,
                              'add_veth', return_value=[ovs_veth, br_veth])
        ) as (mock_exec, mock_add_veth):
            plugin._disconnect_ovs_lb('ovs_veth', 'br_veth', ovs_br, 'brname')

        self.ip_lib.IPWrapper.assert_has_calls([
            mock.call('roothelper')
        ])
        mock_add_veth.assert_has_calls([
            mock.call('ovs_veth', 'br_veth')
        ])
        mock_exec.assert_has_calls([
            mock.call(['brctl', 'delif', 'brname', 'br_veth'],
                      root_helper='roothelper')
        ])
        ovs_br.assert_has_calls([
            mock.call.del_port('ovs_veth')
        ])
        ovs_veth.link.assert_has_calls([
            mock.call.set_down(),
            mock.call.delete()
        ])

    def test_make_vif_args_ovs(self):
        plugin = self.mock_plugin()
        plugin.vif_type = 'ovs'

        expect_inst = {'host': 'host1', 'uuid': 'i-xxxx'}
        expect_net = {'id': 'fake_net', 'bridge': 'brname'}
        expect_map = {'vif_type': 'ovs', 'vif_uuid': 'fake_port',
                      'mac': 'aa:bb:cc:dd:ee:ff',
                      'ovs_interfaceid': 'fake_port'}

        instance, vif = plugin._make_vif_args(
            'i-xxxx', 'fake_net', 'fake_port', 'aa:bb:cc:dd:ee:ff', 'brname')

        self.assertEqual(instance, expect_inst)
        self.assertEqual(vif, (expect_net, expect_map))

    def test_make_vif_args_without_brname(self):
        plugin = self.mock_plugin()
        plugin.vif_type = 'ovs'

        expect_inst = {'host': 'host1', 'uuid': 'i-xxxx'}
        expect_net = {'id': 'fake_net', 'bridge': 'vifbrname'}
        expect_map = {'vif_type': 'ovs', 'vif_uuid': 'fake_port',
                      'mac': 'aa:bb:cc:dd:ee:ff',
                      'ovs_interfaceid': 'fake_port'}

        with mock.patch.object(plugin, '_get_vif_bridge_name',
                               return_value='vifbrname'):
            instance, vif = plugin._make_vif_args(
                'i-xxxx', 'fake_net', 'fake_port', 'aa:bb:cc:dd:ee:ff', None)

        self.assertEqual(instance, expect_inst)
        self.assertEqual(vif, (expect_net, expect_map))

    def test_make_vif_args_lb(self):
        plugin = self.mock_plugin()
        plugin.vif_type = 'bridge'

        expect_inst = {'host': 'host1', 'uuid': 'i-xxxx'}
        expect_net = {'id': 'fake_net', 'bridge': 'brname'}
        expect_map = {'vif_type': 'bridge', 'vif_uuid': 'fake_port',
                      'mac': 'aa:bb:cc:dd:ee:ff'}

        instance, vif = plugin._make_vif_args(
            'i-xxxx', 'fake_net', 'fake_port', 'aa:bb:cc:dd:ee:ff', 'brname')

        self.assertEqual(instance, expect_inst)
        self.assertEqual(vif, (expect_net, expect_map))

    def test_exec_vif_wrapper(self):
        plugin = self.mock_plugin()

        with nested(
            mock.patch(self._AGENT_NAME + '.utils.execute',
                       return_value='qbr'),
            mock.patch(self._AGENT_NAME + '.os.path.join',
                       return_value='vif.py')
        ) as (mock_exec, mock_join):
            plugin._exec_vif_wrapper(['command', 'args'])

        mock_exec.assert_has_calls([
            mock.call(['python', 'vif.py', '--config-file', 'nova.conf',
                       'command', 'args'])
        ])

    def test_vif_plug(self):
        plugin = self.mock_plugin()

        expected = ['i-xxxx', 'fake_net', 'fake_port', 'aa:bb:cc:dd:ee:ff',
                    'brname']

        with nested(
            mock.patch.object(plugin, '_make_vif_args',
                              return_value=['instance', 'vif']),
            mock.patch.object(plugin, '_exec_vif_wrapper')
        ) as (mock_make_vif_args, mock_exec_vif_wrapper):
            plugin._vif_plug(*expected)

        mock_make_vif_args.assert_has_calls([
            mock.call(*expected)
        ])
        mock_exec_vif_wrapper.assert_has_calls([
            mock.call(['plug', 'instance', 'vif'])
        ])

    def test_vif_unplug(self):
        plugin = self.mock_plugin()

        with nested(
            mock.patch.object(plugin, '_make_vif_args',
                              return_value=['instance', 'vif']),
            mock.patch.object(plugin, '_exec_vif_wrapper')
        ) as (mock_make_vif_args, mock_exec_vif_wrapper):
            plugin._vif_unplug('fake_net', 'fake_port', 'brname')

        mock_make_vif_args.assert_has_calls([
            mock.call('dummy-instance-id', 'fake_net', 'fake_port',
                      'un:us:ed:ma:ca:dr', 'brname')
        ])
        mock_exec_vif_wrapper.assert_has_calls([
            mock.call(['unplug', 'instance', 'vif'])
        ])

    def test_create_probe(self):
        plugin = self.mock_plugin()

        br_veth = mock.Mock()
        vm_veth = mock.Mock()
        ns_obj = mock.Mock()

        with nested(
            mock.patch(self._AGENT_NAME + '.utils.execute'),
            mock.patch.object(self.ip_lib.IPWrapper.return_value,
                              'add_veth', return_value=[br_veth, vm_veth]),
            mock.patch.object(self.ip_lib.IPWrapper.return_value,
                              'ensure_namespace', return_value=ns_obj)
        ) as (mock_exec, mock_add_veth, mock_ensure_namespace):
            plugin._create_probe('br_veth', 'vm_veth', 'aa:bb:cc:dd:ee:ff',
                                 'brname', 'nsname')

        self.ip_lib.IPWrapper.assert_has_calls([
            mock.call('roothelper')
        ])
        mock_add_veth.assert_has_calls([
            mock.call('br_veth', 'vm_veth')
        ])
        mock_exec.assert_has_calls([
            mock.call(['brctl', 'addif', 'brname', 'br_veth'],
                      root_helper='roothelper')
        ])
        vm_veth.link.assert_has_calls([
            mock.call.set_address('aa:bb:cc:dd:ee:ff'),
            mock.call.set_up()
        ])
        mock_ensure_namespace.assert_has_calls([
            mock.call('nsname')
        ])
        ns_obj.assert_has_calls([
            mock.call.add_device_to_namespace(vm_veth)
        ])
        br_veth.link.assert_has_calls([
            mock.call.set_up()
        ])

    def test_create_probe_without_bridge(self):
        plugin = self.mock_plugin()

        br_veth = mock.Mock()
        vm_veth = mock.Mock()
        ns_obj = mock.Mock()

        with nested(
            mock.patch(self._AGENT_NAME + '.utils.execute'),
            mock.patch.object(self.ip_lib.IPWrapper.return_value,
                              'add_veth', return_value=[br_veth, vm_veth]),
            mock.patch.object(self.ip_lib.IPWrapper.return_value,
                              'ensure_namespace', return_value=ns_obj)
        ) as (mock_exec, mock_add_veth, mock_ensure_namespace):
            plugin._create_probe('br_veth', 'vm_veth', 'aa:bb:cc:dd:ee:ff',
                                 None, 'nsname')

        self.ip_lib.IPWrapper.assert_has_calls([
            mock.call('roothelper')
        ])
        mock_add_veth.assert_has_calls([
            mock.call('br_veth', 'vm_veth')
        ])
        self.assertEqual(mock_exec.call_count, 0)
        vm_veth.link.assert_has_calls([
            mock.call.set_address('aa:bb:cc:dd:ee:ff'),
            mock.call.set_up()
        ])
        mock_ensure_namespace.assert_has_calls([
            mock.call('nsname')
        ])
        ns_obj.assert_has_calls([
            mock.call.add_device_to_namespace(vm_veth)
        ])
        br_veth.link.assert_has_calls([
            mock.call.set_up()
        ])

    def test_create_probe_without_ns(self):
        plugin = self.mock_plugin()

        br_veth = mock.Mock()
        vm_veth = mock.Mock()
        ns_obj = mock.Mock()

        with nested(
            mock.patch(self._AGENT_NAME + '.utils.execute'),
            mock.patch.object(self.ip_lib.IPWrapper.return_value,
                              'add_veth', return_value=[br_veth, vm_veth]),
            mock.patch.object(self.ip_lib.IPWrapper.return_value,
                              'ensure_namespace', return_value=ns_obj)
        ) as (mock_exec, mock_add_veth, mock_ensure_namespace):
            plugin._create_probe('br_veth', 'vm_veth', 'aa:bb:cc:dd:ee:ff',
                                 'brname', None)

        self.ip_lib.IPWrapper.assert_has_calls([
            mock.call('roothelper')
        ])
        mock_add_veth.assert_has_calls([
            mock.call('br_veth', 'vm_veth')
        ])
        mock_exec.assert_has_calls([
            mock.call(['brctl', 'addif', 'brname', 'br_veth'],
                      root_helper='roothelper')
        ])
        vm_veth.link.assert_has_calls([
            mock.call.set_address('aa:bb:cc:dd:ee:ff'),
            mock.call.set_up()
        ])
        self.assertEqual(mock_ensure_namespace.call_count, 0)
        self.assertEqual(ns_obj.add_device_to_namespace.call_count, 0)
        br_veth.link.assert_has_calls([
            mock.call.set_up()
        ])

    def test_delete_probe(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        br_veth = mock.Mock()
        ip_wrapper.device = mock.Mock(return_value=br_veth)
        ip_wrapper.netns.exists = mock.Mock(return_value=True)
        ip_wrapper_ns = mock.Mock()
        vm_veth = mock.Mock()
        ip_wrapper_ns.device = mock.Mock(return_value=vm_veth)

        with nested(
            mock.patch(self._AGENT_NAME + '.utils.execute'),
            mock.patch.object(self.ip_lib, 'device_exists',
                              side_effect=[True, True]),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              side_effect=[ip_wrapper, ip_wrapper_ns]),
        ) as (mock_exec, mock_device_exists, mock_ipwrapper):
            plugin._delete_probe('br_veth', 'vm_veth', 'brname', 'nsname')

        mock_ipwrapper.assert_has_calls([
            mock.call('roothelper'),
            mock.call('roothelper', 'nsname')
        ])
        mock_device_exists.assert_has_calls([
            mock.call('br_veth', root_helper='roothelper'),
            mock.call('vm_veth', root_helper='roothelper', namespace='nsname')
        ])
        ip_wrapper.assert_has_calls([
            mock.call.device('br_veth'),
            mock.call.netns.exists('nsname')
        ])
        br_veth.link.assert_has_calls([
            mock.call.set_down(),
            mock.call.delete()
        ])
        mock_exec.assert_has_calls([
            mock.call(['brctl', 'delif', 'brname', 'br_veth'],
                      root_helper='roothelper')
        ])
        ip_wrapper_ns.assert_has_calls([
            mock.call.device('vm_veth'),
            mock.call.netns.delete('nsname')
        ])
        vm_veth.link.assert_has_calls([
            mock.call.set_down(),
            mock.call.delete()
        ])

    def test_delete_probe_no_br_veth(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        br_veth = mock.Mock()
        ip_wrapper.device = mock.Mock(return_value=br_veth)
        ip_wrapper.netns.exists = mock.Mock(return_value=True)
        ip_wrapper_ns = mock.Mock()
        vm_veth = mock.Mock()
        ip_wrapper_ns.device = mock.Mock(return_value=vm_veth)

        with nested(
            mock.patch(self._AGENT_NAME + '.utils.execute'),
            mock.patch.object(self.ip_lib, 'device_exists',
                              side_effect=[False, True]),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              side_effect=[ip_wrapper, ip_wrapper_ns]),
        ) as (mock_exec, mock_device_exists, mock_ipwrapper):
            plugin._delete_probe('br_veth', 'vm_veth', 'brname', 'nsname')

        mock_ipwrapper.assert_has_calls([
            mock.call('roothelper'),
            mock.call('roothelper', 'nsname')
        ])
        mock_device_exists.assert_has_calls([
            mock.call('br_veth', root_helper='roothelper'),
            mock.call('vm_veth', root_helper='roothelper', namespace='nsname')
        ])
        self.assertEqual(ip_wrapper.device.call_count, 0)
        self.assertEqual(br_veth.link.set_down.call_count, 0)
        self.assertEqual(br_veth.link.delete.call_count, 0)
        ip_wrapper.assert_has_calls([
            mock.call.netns.exists('nsname')
        ])
        self.assertEqual(mock_exec.call_count, 0)
        ip_wrapper_ns.assert_has_calls([
            mock.call.device('vm_veth'),
            mock.call.netns.delete('nsname')
        ])
        vm_veth.link.assert_has_calls([
            mock.call.set_down(),
            mock.call.delete()
        ])

    def test_delete_probe_no_vm_veth(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        br_veth = mock.Mock()
        ip_wrapper.device = mock.Mock(return_value=br_veth)
        ip_wrapper.netns.exists = mock.Mock(return_value=True)
        ip_wrapper_ns = mock.Mock()
        vm_veth = mock.Mock()
        ip_wrapper_ns.device = mock.Mock(return_value=vm_veth)

        with nested(
            mock.patch(self._AGENT_NAME + '.utils.execute'),
            mock.patch.object(self.ip_lib, 'device_exists',
                              side_effect=[True, False]),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              side_effect=[ip_wrapper, ip_wrapper_ns]),
        ) as (mock_exec, mock_device_exists, mock_ipwrapper):
            plugin._delete_probe('br_veth', 'vm_veth', 'brname', 'nsname')

        mock_ipwrapper.assert_has_calls([
            mock.call('roothelper'),
            mock.call('roothelper', 'nsname')
        ])
        mock_device_exists.assert_has_calls([
            mock.call('br_veth', root_helper='roothelper'),
            mock.call('vm_veth', root_helper='roothelper', namespace='nsname')
        ])
        ip_wrapper.assert_has_calls([
            mock.call.device('br_veth'),
            mock.call.netns.exists('nsname')
        ])
        br_veth.link.assert_has_calls([
            mock.call.set_down(),
            mock.call.delete()
        ])
        mock_exec.assert_has_calls([
            mock.call(['brctl', 'delif', 'brname', 'br_veth'],
                      root_helper='roothelper')
        ])
        self.assertEqual(ip_wrapper_ns.device.call_count, 0)
        self.assertEqual(vm_veth.link.set_down.call_count, 0)
        self.assertEqual(vm_veth.link.delete.call_count, 0)
        ip_wrapper_ns.assert_has_calls([
            mock.call.netns.delete('nsname')
        ])

    def test_delete_probe_no_ns(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        br_veth = mock.Mock()
        ip_wrapper.device = mock.Mock(return_value=br_veth)
        ip_wrapper.netns.exists = mock.Mock(return_value=False)
        ip_wrapper_ns = mock.Mock()
        vm_veth = mock.Mock()
        ip_wrapper_ns.device = mock.Mock(return_value=vm_veth)

        with nested(
            mock.patch(self._AGENT_NAME + '.utils.execute'),
            mock.patch.object(self.ip_lib, 'device_exists',
                              side_effect=[True, True]),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              side_effect=[ip_wrapper, ip_wrapper_ns]),
        ) as (mock_exec, mock_device_exists, mock_ipwrapper):
            plugin._delete_probe('br_veth', 'vm_veth', 'brname', 'nsname')

        mock_ipwrapper.assert_has_calls([
            mock.call('roothelper'),
        ])
        mock_device_exists.assert_has_calls([
            mock.call('br_veth', root_helper='roothelper'),
        ])
        ip_wrapper.assert_has_calls([
            mock.call.device('br_veth'),
            mock.call.netns.exists('nsname')
        ])
        br_veth.link.assert_has_calls([
            mock.call.set_down(),
            mock.call.delete()
        ])
        mock_exec.assert_has_calls([
            mock.call(['brctl', 'delif', 'brname', 'br_veth'],
                      root_helper='roothelper')
        ])
        self.assertEqual(ip_wrapper_ns.device.call_count, 0)
        self.assertEqual(vm_veth.link.set_down.call_count, 0)
        self.assertEqual(vm_veth.link.delete.call_count, 0)
        self.assertEqual(ip_wrapper_ns.netns.delete.call_count, 0)

    def test_delete_probe_without_bridge(self):
        plugin = self.mock_plugin()

        ip_wrapper = mock.Mock()
        br_veth = mock.Mock()
        ip_wrapper.device = mock.Mock(return_value=br_veth)
        ip_wrapper.netns.exists = mock.Mock(return_value=True)
        ip_wrapper_ns = mock.Mock()
        vm_veth = mock.Mock()
        ip_wrapper_ns.device = mock.Mock(return_value=vm_veth)

        with nested(
            mock.patch(self._AGENT_NAME + '.utils.execute'),
            mock.patch.object(self.ip_lib, 'device_exists',
                              side_effect=[True, True]),
            mock.patch.object(self.ip_lib, 'IPWrapper',
                              side_effect=[ip_wrapper, ip_wrapper_ns]),
        ) as (mock_exec, mock_device_exists, mock_ipwrapper):
            plugin._delete_probe('br_veth', 'vm_veth', None, 'nsname')

        mock_ipwrapper.assert_has_calls([
            mock.call('roothelper'),
            mock.call('roothelper', 'nsname')
        ])
        mock_device_exists.assert_has_calls([
            mock.call('br_veth', root_helper='roothelper'),
            mock.call('vm_veth', root_helper='roothelper', namespace='nsname')
        ])
        ip_wrapper.assert_has_calls([
            mock.call.device('br_veth'),
            mock.call.netns.exists('nsname')
        ])
        br_veth.link.assert_has_calls([
            mock.call.set_down(),
            mock.call.delete()
        ])
        self.assertEqual(mock_exec.call_count, 0)
        ip_wrapper_ns.assert_has_calls([
            mock.call.device('vm_veth'),
            mock.call.netns.delete('nsname')
        ])
        vm_veth.link.assert_has_calls([
            mock.call.set_down(),
            mock.call.delete()
        ])

    def test_probe_plug(self):
        plugin = self.mock_plugin()

        with nested(
            mock.patch.object(plugin, '_create_probe'),
            mock.patch.object(plugin, '_get_probe_br_name',
                              return_value='brname')
        ) as (mock_create_probe, mock_get_probe_br_name):
            plugin._probe_plug('fake_net', 'fake_port', 'aa:bb:cc:dd:ee:ff')

        mock_create_probe.assert_has_calls([
            mock.call('qfbfake_port', 'qfvfake_port', 'aa:bb:cc:dd:ee:ff',
                      'brname', 'fakevm-host1-fake_port')
        ])

    def test_probe_unplug(self):
        plugin = self.mock_plugin()

        with nested(
            mock.patch.object(plugin, '_delete_probe'),
            mock.patch.object(plugin, '_get_probe_br_name',
                              return_value='brname')
        ) as (mock_delete_probe, mock_get_probe_br_name):
            plugin._probe_unplug('fake_net', 'fake_port')

        mock_delete_probe.assert_has_calls([
            mock.call('qfbfake_port', 'qfvfake_port', 'brname',
                      'fakevm-host1-fake_port')
        ])

    def test_plug(self):
        plugin = self.mock_plugin()

        with nested(
            mock.patch.object(plugin, '_vif_plug'),
            mock.patch.object(plugin, '_probe_plug')
        ) as (mock_vif_plug, mock_probe_plug):
            plugin.plug('i-xxxx', 'fake_net', 'fake_port',
                        'aa:bb:cc:dd:ee:ff', 'brname')

        mock_vif_plug.assert_has_calls([
            mock.call('i-xxxx', 'fake_net', 'fake_port', 'aa:bb:cc:dd:ee:ff',
                      'brname')
        ])
        mock_probe_plug.assert_has_calls([
            mock.call('fake_net', 'fake_port', 'aa:bb:cc:dd:ee:ff')
        ])

    def test_unplug(self):
        plugin = self.mock_plugin()

        with nested(
            mock.patch.object(plugin, '_probe_unplug'),
            mock.patch.object(plugin, '_vif_unplug')
        ) as (mock_probe_unplug, mock_vif_unplug):
            plugin.unplug('fake_net', 'fake_port', 'brname')

        mock_probe_unplug.assert_has_calls([
            mock.call('fake_net', 'fake_port')
        ])
        mock_vif_unplug.assert_has_calls([
            mock.call('fake_net', 'fake_port', 'brname')
        ])
