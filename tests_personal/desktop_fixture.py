"""仅供明确启动的桌面验收窗口；所有输入均写入指定夹具回执。"""
import argparse
import json
import tkinter as tk
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args()
    root = tk.Tk()
    root.title('UPM Desktop Acceptance Fixture')
    root.geometry('720x480+80+100')
    root.configure(bg='#f5f6f8')
    root.attributes('-topmost', True)
    root.after(250, root.focus_force)
    data = {'clicks': 0, 'right_clicks': 0, 'double_clicks': 0, 'drag': None, 'button_events': []}
    tk.Label(root, text='远程桌面操作验收', font=('Microsoft YaHei UI', 18), bg='#f5f6f8').place(x=24, y=18)
    value = tk.StringVar()
    entry = tk.Entry(root, textvariable=value, font=('Microsoft YaHei UI', 14))
    entry.place(x=24, y=76, width=460, height=38)
    def clicked():
        data['clicks'] += 1
    button = tk.Button(root, text='记录点击', command=clicked)
    button.place(x=506, y=76, width=180, height=38)
    target = tk.Label(root, text='右键 / 双击区域', bg='#e0eaf6', relief='solid')
    target.place(x=24, y=134, width=300, height=70)
    target.bind('<Button-3>', lambda e: data.update(right_clicks=data['right_clicks'] + 1))
    def double(e):
        data['double_clicks'] += 1
        data['button_events'].append(['double', e.time])
    target.bind('<Double-Button-1>', double)
    target.bind('<Button-1>', lambda e: data['button_events'].append(['single', e.time]))
    listing = tk.Listbox(root)
    listing.place(x=356, y=134, width=330, height=70)
    for index in range(100):
        listing.insert('end', f'滚动条目 {index}')
    canvas = tk.Canvas(root, bg='white', width=660, height=170)
    canvas.place(x=24, y=224)
    canvas.create_text(20, 22, anchor='w', text='在此区域内拖动', fill='#263448')
    canvas.bind('<ButtonRelease-1>', lambda e: data.update(drag=[e.x, e.y]))
    status = tk.Label(root, text='仅用于本轮隔离操作验证', bg='#f5f6f8', fg='#596579')
    status.place(x=24, y=428)
    controls = {'entry': entry, 'button': button, 'target': target, 'canvas': canvas, 'listing': listing}
    def publish():
        payload = {**data, 'text': value.get(), 'entry_focused': root.focus_get() is entry,
            'scroll_first': listing.nearest(0), 'controls': {
            name: {'hwnd': widget.winfo_id(), 'logical_center': [
                widget.winfo_rootx() + widget.winfo_width() // 2,
                widget.winfo_rooty() + widget.winfo_height() // 2]} for name, widget in controls.items()}}
        args.receipt.write_text(json.dumps(payload, ensure_ascii=True), encoding='utf-8')
        root.after(80, publish)
    root.after(100, publish)
    root.mainloop()


if __name__ == '__main__':
    main()
