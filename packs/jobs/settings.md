# Settings: "<key> | folder|file|text|longtext|secret | <label> [| required, chat]" (chat: the set-up role may fill it in)
resume_file | file | Master resume (a .md or .txt file) | required
skills | longtext | Skills and experience highlights (only true facts: the writer uses nothing else) | chat
titles | text | Target job titles (comma-separated) | required, chat
locations | text | Locations, or remote | chat
salary | text | Salary range | chat
exclude | text | Companies to exclude (comma-separated) | chat
sources | longtext | Sources to search: job boards or searches, one per line | chat
site_modes | longtext | Mode per site, one per line: "example.com: auto", "site: prepare" or "site: skip"
email_from | text | Your email address (for email applications)
smtp_host | text | Mail server (SMTP), for example smtp.office365.com
smtp_port | text | Mail server port (587, or 465)
smtp_user | text | Mail server user name
smtp_password | secret | Mail server password or app password (kept in Windows Credential Manager)
