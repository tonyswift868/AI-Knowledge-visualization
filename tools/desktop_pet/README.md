# Q 版桌宠原型

这是搜狗输入法小宠物的独立替代原型，不修改搜狗输入法本体，也不向游戏注入按键。

## 当前资源

- `artifacts/desktop_pet/qpet-v1/qpet-v1-binary.png`：运行时使用的金黄色 Q 版母图，已去绿幕并处理 Windows 色键的边缘晕染。
- `artifacts/desktop_pet/qpet-v1/qpet-v1-chroma.png`：绿幕源图，便于后续重新抠图或返工。

## 状态分离

桌宠有两条独立管线：

1. 视觉动画：一直运行，包含呼吸、轻微摆动、整张立绘的刚性联动，以及从肩膀连接到手指的同源袖口/手臂移动、按压与回弹。
2. 键盘观察：普通桌面、游戏前台和独占全屏都只观察 `key-down`；它不会把游戏按键变成文字，也不会向任何窗口注入按键。

键盘钩子只观察 `key-down` 来触发动画，不阻止、不修改、不注入任何按键。游戏窗口的进程名可通过 `--game-process game.exe` 增加；未知全屏窗口默认按游戏模式处理，但游戏模式只改变诊断标记，不会关闭观察。按下哪个键，完整的同源手臂就从肩膀自然伸到对应键帽；松开后回到 F/J 休息位。

`--demo-typing` 只用于验收手部移动：它生成本地合成事件，不发送任何系统按键；即使游戏在前台也不会重新打开真实键盘采集。

## 运行

在 PowerShell 7 中（不要假定安装在 `C:\Program Files\PowerShell\7`；本机当前入口是 PATH 中的 `pwsh.exe`）：

```powershell
pwsh.exe -NoLogo -NoProfile -File "E:\aiPlay\AI-Knowledge visualization\tools\desktop_pet\run_desktop_pet.ps1" -Diagnostic -GameProcess your-game.exe
```

如果当前窗口是 Windows PowerShell 5.1，使用下面这条也可以自动找到本机实际的 PowerShell 7：

```powershell
& (Get-Command pwsh.exe).Source -NoLogo -NoProfile -File "E:\aiPlay\AI-Knowledge visualization\tools\desktop_pet\run_desktop_pet.ps1" -Diagnostic -GameProcess your-game.exe
```

也可以在已经打开的 PowerShell 7 窗口直接运行：

```powershell
.\tools\desktop_pet\run_desktop_pet.ps1 -Diagnostic -GameProcess your-game.exe
```

自检（不启动窗口）：

```powershell
& .\.venv\Scripts\python.exe .\tools\desktop_pet\desktop_pet.py --self-test
```

## 当前边界

当前桌宠使用单张立绘加从原图抠出的左右完整袖口/手臂图层，已经能在键盘上按键、回弹并联动身体；它还不是 Spine/Live2D 的完整骨骼模型。要继续做到每根手指、手腕、头发和衣摆分别联动，需要提供分层源图（身体、左右上臂、前臂、手、头发等）再导入骨骼工具。
