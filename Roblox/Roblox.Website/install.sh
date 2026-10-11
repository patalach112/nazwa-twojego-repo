#!/bin/sh
# builds the website and installs it as a systemd unit.
# edit the paths in RobloxWeb.service first, they are the ones that actually matter
set -e

dotnet publish -c Release -o ./bin/publish
sudo cp ./RobloxWeb.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now RobloxWeb.service
sudo systemctl status RobloxWeb.service --no-pager
