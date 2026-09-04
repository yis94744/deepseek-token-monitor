[Setup]
AppId={{A7E33F1C-4D2B-4C6E-9F8B-2B5A0C77A8D1}
AppName=水豚噜噜 DeepSeek 用量监控
AppVersion=1.13.4
AppPublisher=CapybaraMonitor
DefaultDirName={userpf}\DeepSeekTokenMonitor
DefaultGroupName=水豚噜噜 DeepSeek 用量监控
UninstallDisplayIcon={app}\DeepSeekTokenMonitor.exe
UninstallDisplayName=水豚噜噜 DeepSeek 用量监控
OutputDir=C:\Users\kelang\Documents\Codex\2026-08-13\new-chat-3\outputs\deepseek-token-monitor
OutputBaseFilename=DeepSeekTokenMonitor-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
DisableProgramGroupPage=yes
SetupIconFile=C:\Users\kelang\Documents\Codex\2026-08-13\new-chat-3\outputs\deepseek-token-monitor\assets\icon.ico

[Languages]
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
Name: "startup"; Description: "开机自动启动（后台运行）"; GroupDescription: "附加任务:"; Flags: checkedonce

[Files]
Source: "C:\Users\kelang\Documents\Codex\2026-08-13\new-chat-3\outputs\deepseek-token-monitor\dist\DeepSeekTokenMonitor.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\水豚噜噜监控"; Filename: "{app}\DeepSeekTokenMonitor.exe"
Name: "{group}\卸载水豚噜噜监控"; Filename: "{uninstallexe}"
Name: "{userdesktop}\水豚噜噜监控"; Filename: "{app}\DeepSeekTokenMonitor.exe"; Tasks: desktopicon
Name: "{userstartup}\DeepSeekTokenMonitor"; Filename: "{app}\DeepSeekTokenMonitor.exe"; Tasks: startup

[InstallDelete]
Name: "{userstartup}\DeepSeekTokenMonitor.lnk"; Type: files

[Run]
; 静默安装（自动更新）后同样自动启动新版本，实现"更新完成自动覆盖并重启"
Filename: "{app}\DeepSeekTokenMonitor.exe"; Description: "立即启动水豚噜噜监控"; Flags: nowait postinstall

[UninstallDelete]
Name: "{app}"; Type: filesandordirs