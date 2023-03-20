@REM This script is used to load environment variables from .envrc file
@echo off
for /f "delims=" %%a in ('type .envrc') do set %%a
echo %ENV_VAR