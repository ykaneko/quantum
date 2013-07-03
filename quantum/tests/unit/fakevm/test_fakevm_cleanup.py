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

from quantum.debug.fakevm import fakevm_cleanup_util
from quantum.tests import base


_MODULE_NAME = 'quantum.debug.fakevm.fakevm_cleanup_util'


class TestFakeVMCleanup(base.BaseTestCase):
    def setUp(self):
        super(TestFakeVMCleanup, self).setUp()
        self.addCleanup(mock.patch.stopall)

        self.mock_cfg = mock.patch(_MODULE_NAME + '.cfg').start()
        self.mock_conf = mock.Mock()
        self.mock_cfg.CONF = self.mock_conf

        self.ip_lib = mock.patch(_MODULE_NAME + '.ip_lib').start()
        self.ipwrapper = mock.Mock()
        self.ip_lib.IPWrapper = mock.Mock(return_value=self.ipwrapper)
        self.ovs_lib = mock.patch(_MODULE_NAME + '.ovs_lib').start()
        self.ovsbridge = mock.Mock()
        self.ovs_lib.OVSBridge = mock.Mock(return_value=self.ovsbridge)
        self.config = mock.patch(_MODULE_NAME + '.config').start()
        get_root_helper = mock.Mock(return_value='roothelper')
        self.config.get_root_helper = get_root_helper

    def mock_is_vif_bridge(self):
        match = ['qbr1234']
        unmatch = ['aqbr1234', 'br1234']

        for name in match:
            rc = fakevm_cleanup_util.is_vif_bridge(name)
            self.assertEqual(rc, True)

        for name in unmatch:
            rc = fakevm_cleanup_util.is_vif_bridge(name)
            self.assertEqual(rc, False)

    def mock_is_ovs_port(self):
        match = ['qvo1234']
        unmatch = ['aqvo1234', 'ovs1234']

        for name in match:
            rc = fakevm_cleanup_util.is_ovs_port(name)
            self.assertEqual(rc, True)

        for name in unmatch:
            rc = fakevm_cleanup_util.is_ovs_port(name)
            self.assertEqual(rc, False)

    def mock_is_fakevm_interface(self):
        match = ['qft1234', 'qfb1234', 'qvb1234']
        unmatch = ['aqft1234', 'aqfb1234', 'aqvb1234']

        for name in match:
            rc = fakevm_cleanup_util.is_fakevm_interface(name)
            self.assertEqual(rc, True)

        for name in unmatch:
            rc = fakevm_cleanup_util.is_fakevm_interface(name)
            self.assertEqual(rc, False)

    def mock_is_fakevm_ns(self):
        match = ['fakevm-host1-1234']
        unmatch = ['fakevm', 'fakevm--', 'afakevm-host1-1234']

        for name in match:
            rc = fakevm_cleanup_util.is_fakevm_ns(name)
            self.assertEqual(rc, True)

        for name in unmatch:
            rc = fakevm_cleanup_util.is_fakevm_ns(name)
            self.assertEqual(rc, False)

    def test_main(self):
        devices = []
        for name in ['qbr1', 'qbr2',
                     'qvo1', 'qvo2',
                     'qft1', 'qfb2', 'qvb3']:
            dev = mock.Mock()
            dev.name = name
            devices.append(dev)
        exclude_dev = mock.Mock()
        exclude_dev.name = 'eth0'
        namespaces = ['fakevm-host1-1', 'fakevm-host2-2', 'ns1234']

        with nested(
            mock.patch.object(self.ipwrapper, 'get_devices',
                              return_value=devices + [exclude_dev]),
            mock.patch.object(self.ovs_lib, 'get_bridge_for_iface',
                              return_value='brname'),
            mock.patch(_MODULE_NAME + '.ip_lib.IPWrapper.get_namespaces',
                       return_value=namespaces),
        ) as (mock_get_devices, mock_get_bridge_for_iface,
              mock_get_namespaces):
            fakevm_cleanup_util.main()

        self.mock_conf.assert_has_calls([
            mock.call()
        ])
        self.config.assert_has_calls([
            mock.call.register_root_helper(self.mock_conf),
            mock.call.setup_logging(self.mock_conf),
            mock.call.get_root_helper(self.mock_conf)
        ])
        mock_get_namespaces.assert_has_calls([
            mock.call('roothelper')
        ])
        self.ipwrapper.assert_has_calls([
            mock.call.netns.delete('fakevm-host1-1'),
            mock.call.netns.delete('fakevm-host2-2')
        ])
        self.assertEqual(mock_get_devices.call_count, 3)
        mock_get_bridge_for_iface.assert_has_calls([
            mock.call('roothelper', 'qvo1'),
            mock.call('roothelper', 'qvo2')
        ])
        self.ovs_lib.assert_has_calls([
            mock.call.OVSBridge('brname', 'roothelper')
        ])
        self.ovsbridge.assert_has_calls([
            mock.call.delete_port('qvo1'),
            mock.call.delete_port('qvo2')
        ])
        for dev in devices:
            dev.assert_has_calls([
                mock.call.link.delete()
            ])
        self.assertEqual(exclude_dev.link.delete.call_count, 0)
        self.assertNotIn(mock.call('ns1234'),
                         self.ipwrapper.netns.delete.call_args_list)
