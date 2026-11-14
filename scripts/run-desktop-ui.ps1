$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\desktop")
npm install
npm run dev
