plutil -lint ./com.richard.homely.plist

cp ./com.richard.homely.plist ~/Library/LaunchAgents/

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.richard.homely.plist

launchctl print gui/$(id -u)/com.richard.homely


launchctl unload ~/Library/LaunchAgents/com.richard.homely.plist
launchctl load ~/Library/LaunchAgents/com.richard.homely.plist
launchctl start com.richard.homely
launchctl list | grep homely	
