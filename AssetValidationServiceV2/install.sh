#!/bin/sh
# builds the validator and installs it as a systemd unit.
# edit the paths in AssetValidationServiceV2.service first
set -e

go build -o AssetValidationV2 .
sudo cp ./AssetValidationServiceV2.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now AssetValidationServiceV2.service
sudo systemctl status AssetValidationServiceV2.service --no-pager
