(octopus) richardhine@Mac homely % cat ../../git_token|pbcopy
(octopus) richardhine@Mac homely % printf "protocol=https\nhost=github.com\n" | git credential-osxkeychain erase
(octopus) richardhine@Mac homely % git config --global credential.helper osxkeychain                            
(octopus) richardhine@Mac homely % git push -u origin main                                                      
Username for 'https://github.com': rhine59
Password for 'https://rhine59@github.com': 
branch 'main' set up to track 'origin/main'.
Everything up-to-date

