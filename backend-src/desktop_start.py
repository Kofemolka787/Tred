"""Desktop launcher; no account or model request is made by this window."""
from pathlib import Path
import queue
import subprocess
import sys
import threading
import webbrowser

from ai_provider import load_config

BOT='https://t.me/Tr1edBot_bot'


def model_label(directory):
    config=load_config(directory)
    return f"{config['name']} · {config['model']} · ключ сохранён" if config['api_key'] and config['model'] else 'Настрой ИИ один раз кнопкой ниже'


def run(start,directory):
    try:
        import tkinter as tk
        from tkinter import ttk
        root=tk.Tk()
    except (ImportError,RuntimeError):
        return start(lambda *_:None,threading.Event())
    except Exception as error:
        if type(error).__name__!='TclError':raise
        return start(lambda *_:None,threading.Event())
    root.title('TRED · запуск');root.geometry('570x410');root.minsize(500,380)
    events=queue.Queue();stop=threading.Event();closing=False;route='';finished=False
    frame=ttk.Frame(root,padding=22);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='TRED',font=('Segoe UI',24,'bold')).pack(anchor='w')
    status=tk.StringVar(value='Запускаем сервер…');ai=tk.StringVar();hint=tk.StringVar(value='Дождись готовности. Pocket Option должен быть открыт в этом браузере.')
    ttk.Label(frame,textvariable=status,wraplength=510,font=('Segoe UI',12)).pack(anchor='w',pady=(14,12))
    ttk.Label(frame,textvariable=ai,wraplength=510).pack(anchor='w',pady=8)
    def open_bot():
        if not route:return
        root.clipboard_clear();root.clipboard_append(route);root.update_idletasks()
        webbrowser.open(BOT)
        hint.set('Адрес скопирован. Если бот ещё не подключён: Кабинет → Подключить кабинет → вставить адрес. На телефоне связь подтянется через Telegram. Расширение найдёт сервер на этом ПК само.')
    button=ttk.Button(frame,text='Открыть бота · адрес скопируется',command=open_bot,state='disabled');button.pack(fill='x',pady=8)
    def configure():
        flags=getattr(subprocess,'CREATE_NEW_CONSOLE',0)
        subprocess.Popen([sys.executable,'-B',str(Path(__file__).with_name('configure_ai.py')),'--provider','lunaroute','--data-dir',str(directory)],creationflags=flags)
    ttk.Button(frame,text='Настроить ИИ один раз',command=configure).pack(anchor='w',pady=4)
    ttk.Label(frame,textvariable=hint,wraplength=510).pack(anchor='w',pady=12)
    def close():
        nonlocal closing
        closing=True;stop.set();status.set('Останавливаем TRED…');button.configure(state='disabled')
        if finished:root.destroy()
    ttk.Button(frame,text='Остановить и закрыть',command=close).pack(anchor='e')
    def worker():
        try:
            start(lambda *event:events.put(event),stop)
            events.put(('done','TRED остановлен',None))
        except KeyboardInterrupt:events.put(('done','TRED остановлен',None))
        except Exception as error:
            events.put(('error',str(error) if isinstance(error,ValueError) else 'Ошибка запуска: '+type(error).__name__,None))
    thread=threading.Thread(target=worker,daemon=True);thread.start()
    def refresh():
        nonlocal route,finished
        while True:
            try:stage,message,url=events.get_nowait()
            except queue.Empty:break
            status.set(message)
            if stage in ('ready','waiting'):
                route=url;button.configure(state='normal')
                hint.set('В боте выбери пару или автопоиск и нажми «Получить сигнал». Сохранённый ключ используется автоматически.' if stage=='ready' else 'Адрес пока не проверен с ПК. Бот выполнит собственную проверку подключения; можно попробовать мобильный интернет на телефоне.')
            if stage in ('done','error'):finished=True;button.configure(state='disabled')
        try:ai.set(model_label(directory))
        except (OSError,ValueError):ai.set('Настройки ИИ пока не прочитаны')
        if closing and finished:root.destroy();return
        root.after(500,refresh)
    root.protocol('WM_DELETE_WINDOW',close);root.after(100,refresh);root.mainloop()
    return 0
