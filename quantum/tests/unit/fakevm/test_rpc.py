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

from quantum.fakevm import rpc
from quantum.tests import base


class TestFakeVMRpcApi(base.BaseTestCase):
    def setUp(self):
        super(TestFakeVMRpcApi, self).setUp()
        self.addCleanup(mock.patch.stopall)
        self.mock_proxy = mock.patch(
            'quantum.openstack.common.rpc.proxy').start()

    def test_get_topic_name(self):
        rc = rpc.FakeVMRpcApi.get_topic_name('topic', 'host')
        self.assertEqual(rc, 'quantum.fakevm.rpc.topic.host')

    def test_get_topic_name_without_host(self):
        rc = rpc.FakeVMRpcApi.get_topic_name('topic')
        self.assertEqual(rc, 'quantum.fakevm.rpc.topic')

    def test_init(self):
        rpc.FakeVMRpcApi('topic')

    def test_plug(self):
        rpc_api = rpc.FakeVMRpcApi('topic')

        with nested(
            mock.patch.object(rpc_api, 'call'),
            mock.patch.object(rpc_api, 'make_msg', return_value='message')
        ) as (mock_call, mock_make_msg):
            rpc_api.plug('context', 'host1', 'i-xxxx', 'fake_net',
                         'fake_port', 'aa:bb:cc:dd:ee:ff', 'brname')

        mock_call.assert_has_calls([
            mock.call('context', 'message',
                      topic='quantum.fakevm.rpc.topic.host1')
        ])
        mock_make_msg.assert_has_calls([
            mock.call('plug', instance_id='i-xxxx', network_id='fake_net',
                      vif_uuid='fake_port', mac='aa:bb:cc:dd:ee:ff',
                      bridge_name='brname')
        ])

    def test_plug_without_bridge(self):
        rpc_api = rpc.FakeVMRpcApi('topic')

        with nested(
            mock.patch.object(rpc_api, 'call'),
            mock.patch.object(rpc_api, 'make_msg', return_value='message')
        ) as (mock_call, mock_make_msg):
            rpc_api.plug('context', 'host1', 'i-xxxx', 'fake_net',
                         'fake_port', 'aa:bb:cc:dd:ee:ff')

        mock_call.assert_has_calls([
            mock.call('context', 'message',
                      topic='quantum.fakevm.rpc.topic.host1')
        ])
        mock_make_msg.assert_has_calls([
            mock.call('plug', instance_id='i-xxxx', network_id='fake_net',
                      vif_uuid='fake_port', mac='aa:bb:cc:dd:ee:ff',
                      bridge_name=None)
        ])

    def test_unplug(self):
        rpc_api = rpc.FakeVMRpcApi('topic')

        with nested(
            mock.patch.object(rpc_api, 'call'),
            mock.patch.object(rpc_api, 'make_msg', return_value='message')
        ) as (mock_call, mock_make_msg):
            rpc_api.unplug('context', 'host1', 'fake_net', 'fake_port',
                           'brname')

        mock_call.assert_has_calls([
            mock.call('context', 'message',
                      topic='quantum.fakevm.rpc.topic.host1')
        ])
        mock_make_msg.assert_has_calls([
            mock.call('unplug', network_id='fake_net', vif_uuid='fake_port',
                      bridge_name='brname')
        ])

    def test_unplug_without_bridge(self):
        rpc_api = rpc.FakeVMRpcApi('topic')

        with nested(
            mock.patch.object(rpc_api, 'call'),
            mock.patch.object(rpc_api, 'make_msg', return_value='message')
        ) as (mock_call, mock_make_msg):
            rpc_api.unplug('context', 'host1', 'fake_net', 'fake_port')

        mock_call.assert_has_calls([
            mock.call('context', 'message',
                      topic='quantum.fakevm.rpc.topic.host1')
        ])
        mock_make_msg.assert_has_calls([
            mock.call('unplug', network_id='fake_net', vif_uuid='fake_port',
                      bridge_name=None)
        ])

    def test_unplug_all_host(self):
        rpc_api = rpc.FakeVMRpcApi('topic')

        with nested(
            mock.patch.object(rpc_api, 'fanout_cast'),
            mock.patch.object(rpc_api, 'make_msg', return_value='message')
        ) as (mock_fanout_cast, mock_make_msg):
            rpc_api.unplug_all_host('context', 'fake_net', 'fake_port',
                                    'brname')

        mock_fanout_cast.assert_has_calls([
            mock.call('context', 'message', topic='quantum.fakevm.rpc.topic')
        ])
        mock_make_msg.assert_has_calls([
            mock.call('unplug_all_host', network_id='fake_net',
                      vif_uuid='fake_port', bridge_name='brname')
        ])

    def test_exec_command(self):
        rpc_api = rpc.FakeVMRpcApi('topic')

        with nested(
            mock.patch.object(rpc_api, 'call'),
            mock.patch.object(rpc_api, 'make_msg', return_value='message')
        ) as (mock_call, mock_make_msg):
            rpc_api.exec_command('context', 'host1', 'fake_port',
                                 'invoke_command')

        mock_call.assert_has_calls([
            mock.call('context', 'message',
                      topic='quantum.fakevm.rpc.topic.host1')
        ])
        mock_make_msg.assert_has_calls([
            mock.call('exec_command', vif_uuid='fake_port',
                      command='invoke_command')
        ])
