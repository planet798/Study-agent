#ifndef AppVersion
  #error AppVersion is required
#endif

[Setup]
AppId={{81A69D23-463D-4180-B995-045B4C44E919}
AppName=Study Agent
AppVersion={#AppVersion}
AppPublisher=planet798
AppPublisherURL=https://github.com/planet798/Study-agent
DefaultDirName={localappdata}\Programs\StudyAgent
DefaultGroupName=Study Agent
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\installer
OutputBaseFilename=StudyAgent-Setup-{#AppVersion}-x64
SetupIconFile=..\assets\study-agent.ico
UninstallDisplayIcon={app}\StudyAgent.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "..\dist\StudyAgent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Study Agent"; Filename: "{app}\StudyAgent.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\Study Agent"; Filename: "{app}\StudyAgent.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\StudyAgent.exe"; Description: "启动 Study Agent"; Flags: nowait postinstall skipifsilent

; 不配置 UninstallDelete：卸载只删除安装器登记的文件，保留用户学习数据。
