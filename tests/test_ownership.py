import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app.api import server, auth
from app.api.event_store import EventStore
from app.utils.path_utils import resolve_path

class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(auth, 'DB_PATH', self.root/'accounts.db'),
                        patch.object(server.manager, 'store', EventStore(self.root/'events.db')),
                        patch.object(server, 'output_dir', self.root/'output'),
                        patch.object(server, 'updated_dir', self.root/'updated')]
        for p in self.patches: p.start()
        server.active_tasks.clear()
        server.manager.active_connections.clear()
        server.manager.send_locks.clear()
        server.manager.loop = None
    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()
    def register(self, client, name):
        response=client.post('/api/auth/register', json={'username':name,'password':'test-password-123'})
        self.assertEqual(response.status_code,200,response.text)
    def test_accounts_ownership_and_files(self):
        async def fake_agent(query, thread):
            await asyncio.Event().wait()
        with patch.object(server,'run_deep_agent',fake_agent), TestClient(server.app) as alice, TestClient(server.app) as bob:
            self.assertEqual(alice.get('/api/conversations').status_code,401)
            self.register(alice,'alice'); self.register(bob,'bob')
            self.assertEqual(alice.post('/api/task',json={'query':'第一个问题','thread_id':'alice-task'}).status_code,200)
            self.assertEqual(alice.post('/api/task/alice-task/cancel').status_code,200)
            self.assertEqual(alice.post('/api/task',json={'query':'第二个问题','thread_id':'alice-task'}).status_code,200)
            self.assertEqual(alice.get('/api/conversations').json()[0]['title'],'第一个问题')
            self.assertEqual(bob.get('/api/conversations').json(),[])
            self.assertEqual(bob.get('/api/conversations/alice-task/uploads').status_code,404)
            self.assertEqual(alice.post('/api/logout',headers={'Origin':'https://evil.example'}).status_code,403)
            self.assertEqual(bob.post('/api/task',json={'query':'case attack','thread_id':'ALICE-task'}).status_code,400)
            for url in ('/api/task/alice-task/cancel','/api/task'):
                response=bob.post(url,json={'query':'steal','thread_id':'alice-task'})
                self.assertEqual(response.status_code,404)
            self.assertEqual(bob.post('/api/upload',data={'thread_id':'alice-task'},files={'files':('x.txt',b'x')}).status_code,404)
            self.assertEqual(alice.post('/api/upload',data={'thread_id':'../escape'},files={'files':('x.txt',b'x')}).status_code,400)
            self.assertEqual(alice.post('/api/upload',data={'thread_id':'alice-task'},files={'files':('../escape.txt',b'x')}).status_code,400)
            self.assertEqual(alice.post('/api/upload',data={'thread_id':'alice-task'},files={'files':('note.txt',b'private')}).status_code,200)
            output=self.root/'output'/'session_alice-task';output.mkdir(parents=True)
            artifact=output/'answer.md';artifact.write_text('private answer')
            for file in (artifact,self.root/'updated'/'session_alice-task'/'note.txt'):
                self.assertEqual(alice.get('/api/download',params={'path':str(file)}).status_code,200)
                self.assertEqual(bob.get('/api/download',params={'path':str(file)}).status_code,404)
                self.assertEqual(bob.get('/api/files',params={'path':str(file.parent)}).status_code,404)
            self.assertEqual(alice.get('/api/files',params={'path':str(output.parent)}).status_code,403)
            with self.assertRaises(WebSocketDisconnect):
                with bob.websocket_connect('/ws/alice-task'): pass
            with alice.websocket_connect('/ws/alice-task') as ws:
                self.assertTrue(ws.receive_json()['is_running'])
            alice.post('/api/task/alice-task/cancel')
            alice.post('/api/logout')
            self.assertEqual(alice.get('/api/conversations').status_code,401)
            self.assertEqual(alice.post('/api/auth/login',json={'username':'alice','password':'wrong-password'}).status_code,401)
            self.assertEqual(alice.post('/api/auth/login',json={'username':'alice','password':'test-password-123'}).status_code,200)
            self.assertEqual(len(alice.get('/api/conversations').json()),1)
    def test_legacy_history_cannot_be_claimed_and_tool_paths(self):
        server._publish_event('legacy','task_started','old',{'query':'private'})
        with TestClient(server.app) as client:
            self.register(client,'alice')
            self.assertEqual(client.post('/api/task',json={'query':'claim','thread_id':'legacy'}).status_code,403)
        session=self.root/'output'/'session_a'
        self.assertEqual(resolve_path('a.md',str(session)),str(session/'a.md'))
        for path in ('../session_b/private.txt',str(self.root/'output'/'session_b'/'private.txt')):
            with self.assertRaises(ValueError): resolve_path(path,str(session))

if __name__=='__main__': unittest.main()
