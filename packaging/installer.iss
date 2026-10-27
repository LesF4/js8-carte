; Installeur Windows de JS8 Carte — Inno Setup 6 (https://jrsoftware.org).
; Construction : python packaging\build.py --installer
; Prérequis    : packaging\dist\JS8Carte\ (produit par PyInstaller via build.py).

#ifndef AppVersion
  #define AppVersion "0.8.3"
#endif
#define AppName      "JS8Carte"
#define AppFullName  "JS8 Carte"
#define AppPublisher "F4MAJ / Jean-Bernard"
#define AppURL       "https://github.com/LesF4/js8-carte"
#define AppExe       "JS8Carte.exe"

[Setup]
; AppId fige l'identité (mises à jour / désinstallation). NE PAS changer entre versions.
AppId={{C5319C6C-BD6C-4621-82B2-BD0A95C08FFA}
AppName={#AppFullName}
AppVersion={#AppVersion}
AppVerName={#AppFullName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppCopyright=© 2026 F4MAJ / Jean-Bernard — MIT
VersionInfoCompany={#AppPublisher}
VersionInfoCopyright=© 2026 F4MAJ / Jean-Bernard — MIT
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppFullName}
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppFullName} {#AppVersion}
OutputDir=dist
OutputBaseFilename=JS8Carte-Setup-{#AppVersion}
SetupIconFile=icon.ico
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Installation par utilisateur : pas de pop-up UAC, pas besoin d'être admin.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
; Ferme l'app si elle tourne pendant une mise à jour / désinstallation.
CloseApplications=yes

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
fr.ReadGuide=Lire le guide (réglage de JS8Call à faire une fois)
en.ReadGuide=Read the guide (one-time JS8Call setting)
fr.GuideName=Guide
en.GuideName=Guide

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\JS8Carte\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
Source: "LISEZ-MOI.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppFullName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:GuideName}"; Filename: "{app}\LISEZ-MOI.txt"
Name: "{group}\{cm:UninstallProgram,{#AppFullName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppFullName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\LISEZ-MOI.txt"; Description: "{cm:ReadGuide}"; Flags: postinstall shellexec skipifsilent nowait unchecked
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppFullName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Réglages, carnet des locators, journal et cache QRZ vivent dans %APPDATA%\JS8Carte :
; on ne les touche PAS à la désinstallation.
Type: dirifempty; Name: "{app}"
