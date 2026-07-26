' Abrir Compactador de PDF.vbs
'
' Este arquivo e o "atalho" que voce deve usar no dia a dia: ele abre o
' programa sem mostrar nenhuma janela preta (cmd) na tela.
'
' Na PRIMEIRA vez que voce usa (quando os componentes ainda nao foram
' instalados), ele mostra a janela normalmente, para voce acompanhar a
' instalacao e ver qualquer erro que aparecer. Da segunda vez em diante,
' abre direto, igual um programa comum.

Option Explicit

Dim fso, shell, folder, pythonwPath, batPath, q

Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

folder = fso.GetParentFolderName(WScript.ScriptFullName)
q = Chr(34)
pythonwPath = folder & "\venv\Scripts\pythonw.exe"
batPath = q & folder & "\Compactador de PDF.bat" & q

If fso.FileExists(pythonwPath) Then
    ' Ja configurado antes: abre totalmente escondido, sem nenhuma janela.
    shell.Run batPath, 0, False
Else
    ' Primeira vez: mostra a janela para acompanhar a instalacao dos
    ' componentes e ver qualquer mensagem de erro, caso aconteca algum.
    shell.Run batPath, 1, False
End If
