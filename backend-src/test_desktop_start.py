from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from configure_ai import save
from desktop_start import model_label
import desktop_start
import free_start


class DesktopStartupTests(unittest.TestCase):
    def test_saved_model_status_does_not_display_key_or_send_model_requests(self):
        with tempfile.TemporaryDirectory() as tmp:
            key='lr_fixture_private_startup_key'
            save(tmp,key,'deepseek-4.1-flash',allowed_users=[101],provider='lunaroute')
            label=model_label(tmp)
            self.assertIn('deepseek-4.1-flash',label);self.assertNotIn(key,label)

    def test_closing_before_start_does_not_launch_server_or_tunnel(self):
        stop=threading.Event();stop.set()
        with patch('sys.argv',['free_start.py','--local-only']),patch.object(free_start.subprocess,'Popen') as spawn:
            with self.assertRaises(KeyboardInterrupt):free_start.main(status=lambda *_:None,stop=stop)
            spawn.assert_not_called()

    def test_closing_ready_window_stops_its_backend_and_tunnel(self):
        from test_free_start import Process
        stop=threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            backend,tunnel=Process(),Process('https://fixture-route.trycloudflare.com\nINF Registered tunnel connection protocol=http2\n')
            def status(stage,*_):
                if stage=='ready':stop.set()
            with patch('sys.argv',['free_start.py','--data-dir',tmp]),patch.object(free_start,'check_local',side_effect=[False,True]),patch.object(free_start,'tunnel_binary',return_value=Path('fixture')),patch.object(free_start.subprocess,'Popen',side_effect=[backend,tunnel]),patch.object(free_start,'public_probe',return_value='OK'):
                with self.assertRaises(KeyboardInterrupt):free_start.main(status=status,stop=stop)
            self.assertTrue(backend.terminated and tunnel.terminated)

    def test_launcher_buttons_and_graceful_close_without_a_native_display(self):
        widgets=[];values=[]
        class Var:
            def __init__(self,value=''):self.value=value
            def set(self,value):self.value=value;values.append(value)
        class Widget:
            def __init__(self,*args,**kw):self.kw=kw;widgets.append(self)
            def pack(self,**kw):pass
            def configure(self,**kw):self.kw.update(kw)
        class Root:
            def __init__(self):self.calls=[];self.closed=False;self.clipboard='';self.handler=None
            def title(self,*_):pass
            def geometry(self,*_):pass
            def minsize(self,*_):pass
            def protocol(self,name,handler):self.handler=handler
            def after(self,delay,callback):self.calls.append(callback)
            def clipboard_clear(self):self.clipboard=''
            def clipboard_append(self,value):self.clipboard+=value
            def update_idletasks(self):pass
            def destroy(self):self.closed=True
            def mainloop(self):
                opened=False
                for _ in range(3000):
                    if self.closed:return
                    if self.calls:self.calls.pop(0)()
                    button=next((w for w in widgets if w.kw.get('text','').startswith('Открыть бота')),None)
                    if button and button.kw.get('state')=='normal' and not opened:
                        opened=True;button.kw['command']();self.handler()
                    time.sleep(.001)
                raise AssertionError('Launcher did not finish its worker')
        root=Root();tk=SimpleNamespace(Tk=lambda:root,StringVar=Var,ttk=SimpleNamespace(Frame=Widget,Label=Widget,Button=Widget))
        stopped=[]
        def start(callback,event):
            callback('ready','Готово','https://fixture-route.trycloudflare.com')
            stopped.append(event.wait(2))
            raise KeyboardInterrupt()
        with patch.dict('sys.modules',{'tkinter':tk}),patch.object(desktop_start,'model_label',return_value='LunaRoute · model · ключ сохранён'),patch.object(desktop_start.webbrowser,'open') as opened:
            self.assertEqual(desktop_start.run(start,Path('/tmp/fixture')),0)
            opened.assert_called_once_with(desktop_start.BOT)
        self.assertEqual(stopped,[True]);self.assertTrue(root.closed)
        self.assertEqual(root.clipboard,'https://fixture-route.trycloudflare.com')

