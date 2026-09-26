# Splash screens

| File | Shown |
| --- | --- |
| `power-on-hi.jpg` | when the camera switches on |
| `power-off-bye.jpg` | when the camera switches off |

They replace the stock screens in place (stock resources 65 and 64), so a new
picture must match the stock format exactly: **320×240 baseline JPEG, 4:2:0
colour subsampling, no larger than the stock slot** (28,433 bytes for the power-on
screen, 32,103 bytes for the power-off screen). After replacing a file, update its
`sha256` and `bytes` in `manifest.json`; the build checks both.
