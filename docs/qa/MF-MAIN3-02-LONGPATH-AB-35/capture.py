import ctypes,json,hashlib,sys
from ctypes import wintypes as W
from pathlib import Path
from datetime import datetime,timezone

from PIL import Image
O=Path(__file__).parent;device,name,hwnd=sys.argv[1],sys.argv[2],int(sys.argv[3]);p=json.loads((O/f'devices/{device}/process.json').read_text())
u=ctypes.windll.user32;g=ctypes.windll.gdi32
pid=W.DWORD();u.GetWindowThreadProcessId(W.HWND(hwnd),ctypes.byref(pid));assert pid.value==p['pid'];k=ctypes.windll.kernel32;k.OpenProcess.restype=W.HANDLE;ph=k.OpenProcess(0x1000,False,pid.value);bufpath=ctypes.create_unicode_buffer(32768);plen=W.DWORD(32768);assert k.QueryFullProcessImageNameW(ph,0,bufpath,ctypes.byref(plen));assert Path(bufpath.value)==Path(p['artifact']);k.CloseHandle(ph);assert hashlib.sha256(Path(p['artifact']).read_bytes()).hexdigest()==p['sha256']
rect=W.RECT();assert u.GetWindowRect(W.HWND(hwnd),ctypes.byref(rect));w,h=rect.right-rect.left,rect.bottom-rect.top
u.GetWindowDC.restype=W.HDC;g.CreateCompatibleDC.restype=W.HDC;g.CreateCompatibleBitmap.restype=W.HBITMAP;g.SelectObject.restype=W.HGDIOBJ
g.CreateCompatibleDC.argtypes=[W.HDC];g.CreateCompatibleBitmap.argtypes=[W.HDC,ctypes.c_int,ctypes.c_int];g.SelectObject.argtypes=[W.HDC,W.HGDIOBJ];u.PrintWindow.argtypes=[W.HWND,W.HDC,W.UINT];g.GetDIBits.argtypes=[W.HDC,W.HBITMAP,W.UINT,W.UINT,ctypes.c_void_p,ctypes.c_void_p,W.UINT];g.DeleteObject.argtypes=[W.HGDIOBJ];g.DeleteDC.argtypes=[W.HDC];u.ReleaseDC.argtypes=[W.HWND,W.HDC]
dc=u.GetWindowDC(W.HWND(hwnd));mem=g.CreateCompatibleDC(dc);bitmap=g.CreateCompatibleBitmap(dc,w,h);old=g.SelectObject(mem,bitmap)
class BI(ctypes.Structure):_fields_=[('size',W.DWORD),('width',W.LONG),('height',W.LONG),('planes',W.WORD),('bitcount',W.WORD),('compression',W.DWORD),('sizeimage',W.DWORD),('xppm',W.LONG),('yppm',W.LONG),('used',W.DWORD),('important',W.DWORD)]
try:
 assert u.PrintWindow(W.HWND(hwnd),mem,2)
 info=BI(40,w,-h,1,32,0,0,0,0,0,0);buf=ctypes.create_string_buffer(w*h*4)
 assert g.GetDIBits(mem,bitmap,0,h,buf,ctypes.byref(info),0)
 path=O/f'evidence/staging/{name}.png';Image.frombuffer('RGB',(w,h),buf.raw,'raw','BGRX',0,1).save(path)
 report=dict(path=str(path),window_rect=[rect.left,rect.top,rect.right,rect.bottom],pid=pid.value,hwnd=hwnd,width=w,height=h,artifact_sha256=p['sha256'],image_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),captured_at=datetime.now(timezone.utc).isoformat(),uia_rect=json.loads((O/'evidence/staging/frame-uia.json').read_text(encoding='utf-8'))['window_rect']);path.with_suffix('.frame.json').write_text(json.dumps(report));print(json.dumps(report))
finally:
 g.SelectObject(mem,old);g.DeleteObject(bitmap);g.DeleteDC(mem);u.ReleaseDC(W.HWND(hwnd),dc)
