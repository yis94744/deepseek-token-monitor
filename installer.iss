[Setup]
AppId={{A7E33F1C-4D2B-4C6E-9F8B-2B5A0C77A8D1}
AppName=水豚噜噜 DeepSeek 用量监控
AppVersion=1.13.25
AppPublisher=CapybaraMonitor
DefaultDirName={userpf}\DeepSeekTokenMonitor
DefaultGroupName=水豚噜噜 DeepSeek 用量监控
UninstallDisplayIcon={app}\DeepSeekTokenMonitor.exe
UninstallDisplayName=水豚噜噜 DeepSeek 用量监控
OutputDir=.
OutputBaseFilename=DeepSeekTokenMonitor-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
Uninstallable=yes
ArchitecturesAllowed=x64compatible
DisableProgramGroupPage=yes
SetupIconFile=assets\icon.ico

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
Name: "startup"; Description: "开机自动启动（后台运行）"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
Source: "dist\DeepSeekTokenMonitor.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\水豚噜噜监控"; Filename: "{app}\DeepSeekTokenMonitor.exe"
Name: "{group}\卸载水豚噜噜监控"; Filename: "{uninstallexe}"
Name: "{userdesktop}\水豚噜噜监控"; Filename: "{app}\DeepSeekTokenMonitor.exe"; Tasks: desktopicon
Name: "{userstartup}\DeepSeekTokenMonitor"; Filename: "{app}\DeepSeekTokenMonitor.exe"; Tasks: startup

[InstallDelete]
Name: "{userstartup}\DeepSeekTokenMonitor.lnk"; Type: files

[Run]
; 注意（v1.13.24）：此处【不再】安装完立即启动。
; 原因：新版是 PyInstaller onefile，启动时要解压约 1000 个文件，其中
; python313.dll 排在解包清单 98.4% 处。若安装刚结束就立刻启动，解包过程
; 可能被"杀软扫描刚写入的 exe / 旧进程残留句柄"打断，导致该 DLL 未解出，
; 弹出 "Failed to load Python DLL ... LoadLibrary: 找不到指定的模块"
; （Windows 在目标文件不存在时即返回 126）。
; 自动更新场景改由程序内的延迟更新器（run_setup_update.ps1）在安装完成、
; 文件稳定后启动；用户手动安装时则在安装向导末页勾选启动（非静默时才显示）。
Filename: "{app}\DeepSeekTokenMonitor.exe"; Description: "立即启动水豚噜噜监控"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Name: "{app}"; Type: filesandordirs