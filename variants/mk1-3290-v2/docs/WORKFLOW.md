# Install, customize and verify

Commands below run from `variants/mk1-3290-v2/`. Linux, Python ≥3.10,
libusb, PyUSB, Pillow and NumPy are required. Install the variant requirements
from its README. Each output/run directory must be new; never overwrite evidence.
Do not run hardware commands while an unrelated camera is connected.

## 1. Identify and back up your own unit

The reference is one **MK1-3290-V2 / AX3291A / ZB25VQ32** firmware revision.
The included `flash_read1.bin` is an immutable 4 MiB compatibility/build reference,
not a substitute for backing up another camera.

On stock firmware insert an SD card, power on and connect a data USB cable.
Storage mode on our unit was `0219:3280`, interface 4; without a card it was
webcam `1908:3282`, which cannot be flashed with these tools. Ask the human to
**eject the SD volume in the file manager and confirm**, leaving the card inserted.
The session also checks that Linux reports the medium ejected (zero block size);
merely unmounting it is insufficient. Optional USB permissions:

```bash
sudo cp tools/99-mk1-camera.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
```

Follow the rule's group permissions and reconnect if needed. Do not use a USB
reset as an installation step. Check the stock helper code and create reference
builds (the probe only reads RAM):

```bash
python3 tools/build_variant.py
python3 tools/usb_probe.py
python3 tools/mk1_usb_flash.py backup --output runs/stock-backup
```

The backup command reads the complete SPI flash twice independently through the
stock SPI helper; it is not a copy of the SD volume. It compares the reads and
compares against `flash_read1.bin`. Backup reads use temporary owned RAM and restore
scratch state on certain completion; they do not write flash. The output contains
`flash-read-1.bin`, `flash-read-2.bin`, `backup.bin`, `report.json` and `journal.jsonl`.
Expect `completed: true`, `reads_identical: true` and only the settings sector as a
possible difference. Keep the folder somewhere safe.

If the reads differ, stop. If bytes differ outside settings, investigate another
revision or previously modified firmware before writing. Do not remove the hash
gate. If USB itself is inaccessible, SPI/Pico reading is a recovery/research route;
see the repository's [Pico guide](../../../docs/RECOVERY.md), substituting MK1 layout
and firmware. Its penguin pin photographs do not prove wiring on this board.

## 2. Reproduce our complete variant: initial stock installation

This section uses the **public reference artwork (blurred faces)** and the same
application effects we installed. Its full-image hashes differ from the historical
private installation; the application is identical. The public artwork has only
been verified offline, not installed during this repository privacy update.
For an already installed build_04, skip to customization and use its exact retained
image as the known preimage. For graphics only while
retaining an entirely stock application, see the end of section 4.

Build once, check the hashes in README.md, and run offline tests:

```bash
python3 tools/build_variant.py
python3 -m unittest discover -s tools -p 'test_mk1*.py'
python3 tools/trace_mk1_boot_header.py \
  --candidate analysis/build_04/image.bin \
  --rom ../../assets/rom/soc_boot_rom.bin --output analysis/boot-parser
```

The ROM parser test checks the real header parser, including a forced-CRC failure
control. It does not run SDRAM calibration or the full application.

Review `analysis/build_01/manifest.json`: 240 changed 4 KiB resource sectors;
boot/application/settings are unchanged. The original installer runs three phases,
**190 relocated sectors, 49 in-place sectors, then one resource-directory sector**.
Get separate human approval for each phase before issuing its command:

```bash
# Only after approval for relocated resources:
python3 tools/mk1_usb_flash.py install --backup runs/stock-backup \
  --phase relocated --approved-phase --source-packet 512 --output runs/resources-relocated

# Only after approval for in-place resources:
python3 tools/mk1_usb_flash.py install --backup runs/stock-backup \
  --phase inplace --approved-phase --source-packet 512 --output runs/resources-inplace

# Only after approval to publish the resource directory:
python3 tools/mk1_usb_flash.py install --backup runs/stock-backup \
  --phase directory --approved-phase --source-packet 512 --output runs/resources-directory
```

Each phase compares two fresh full reads with its expected preceding state,
verifies source RAM before writes, verifies every sector twice, then verifies a
full staged image. Keep the camera connected and idle between phases; a settings
change can cause this historical path to stop for review. The final directory
report must say it matches the target with live settings. Writes are not atomic:
interrupted in-place/relocated updates can leave mixed resources; directory-last
ordering reduces risk but does not make interruption harmless.

Next rehearse the native effects in private RAM. This does not install live UI
hooks or write flash; a successful report and cleanup are required by activation:

```bash
python3 tools/mk1_effects_rehearsal.py --output runs/effects-rehearsal
python3 tools/mk1_application_flash.py plan --candidate analysis/build_04
```

Review the plan: payload sectors `0x23000`, `0x78000`, `0x79000`, `0x7a000`,
`0x7b000`; activation sectors `0x6000`, `0xa000`, `0xb000`, `0x41000`, `0x5a000`,
`0x4b000`. Retirement/new code precedes hooks; USB selection is installed last.
Get approval for each of these two writing phases:

```bash
# Only after approval for the five payload sectors:
python3 tools/mk1_application_flash.py install --candidate analysis/build_04 \
  --phase payload --approved-phase --rehearsal runs/effects-rehearsal \
  --output runs/effects-payload

# Only after approval for the six activation sectors:
python3 tools/mk1_application_flash.py install --candidate analysis/build_04 \
  --phase activate --approved-phase --rehearsal runs/effects-rehearsal \
  --previous runs/effects-payload --output runs/effects-activate
```

Do not use the historical label-update script here: build_04 already has the
transparent white label. Payload takes two independent full backups; activation
checks a fresh full read against the preceding verified stage, plus its retained
paired backup. Both verify each sector and a full image. Flashing does not modify
the running application's hooks; restart only after all required phases succeed.

## 3. Restart and check the finished camera

After full verification, turn off/on and reconnect. Initially keep the card
inserted and eject its volume again before checks:

```bash
python3 tools/mk1_verify_effects_boot.py \
  --candidate analysis/build_04 --output runs/boot-with-card
```

Physically check left/right selection, normal mode, seven frames, three effects,
printing both on/off, saved SD photos, print orientation, and date setting on/off.
The original information bar should be unchanged; white transparent labels are
below it and absent from photos/prints. Keep the menu and SD features usable.

Then disconnect, remove the SD card, cold restart, reconnect **without the card**
and record `lsusb`. After the owner confirms the card is absent:

```bash
python3 tools/mk1_verify_effects_boot.py --sd-absent \
  --candidate analysis/build_04 --output runs/boot-no-card
python3 tools/mk1_no_sd_access_check.py \
  --boot-check runs/boot-no-card --output runs/no-card-access
```

The second check verifies reads and write-refusal guards; it performs no flash
writes. It needs a successful no-card boot report. These checks were still pending
in our recorded installation; the descriptor-selection patch is installed, but
that is not proof of post-restart no-card operation. A webcam ID after restart is
cause to investigate the running image, not to try a different model's installer.

## 4. Make your own frames and graphics

Copy `assets/source/` to a new editing folder, keep the originals, and replace
pictures using the same numbered names. The supplied controls offer **exactly
seven frames**; changing that count needs a separate code change and tests.
Use genuine transparent PNGs for see-through frame regions. An opaque black PNG
is artwork and is brightened: it is not treated as transparent by this converter.

| Editable file | Purpose / converted format |
| --- | --- |
| `001.png`–`007.png` | Seven frames, resized to 1280×720 baseline JPEG 4:2:0 |
| `011.png` | Menu background, centre-cropped to 4:3 then resized to 320×240 JPEG |
| `032.bmp`, `034.bmp`–`038.bmp` | Six menu buttons, converted to exact 96×96 stock BMPs |
| `048.png`, `049.png` | Power-on and power-off graphics, 320×240 JPEG |
| Generated `033.jpg` | Composite menu: new 011 background plus all six buttons |

Conversion scales frames to 16:9. The source alpha selects the photo opening;
decoded frame luminance **Y≤26** is transparent in the stock firmware. The converter
makes the background black, lifts artwork brightness, and reports pixels that
would disappear or leak. Review every `preview/*_frame_on_camera.png` and
`report.json`; do not proceed with nonzero transparency errors. JPEGs must be
baseline, never progressive. Button transparency uses exact grey `(140,140,140)`.

```bash
mkdir -p build
cp -R assets/source build/my-sources
# Edit pictures in build/my-sources, then:
python3 tools/prepare_new_content.py build/my-sources build/my-converted
python3 tools/build_custom_image.py build/my-converted/firmware build/my-camera
```

The converter recreates 033 only when 011 and all six buttons are present.
A complete source set prevents the composite menu from falling back to an old
picture. Keep the **complete replacement set** for every build: omitted IDs revert
to stock, including hidden/visible frames. If changing just one prepared resource,
copy `assets/prepared/`, replace that file in the copy and build from the copy;
do not double-convert existing frame JPEGs.

The resource builder always starts from immutable stock, uses stock slots when
possible, and relocates larger resources into erased tail space at `0x2fe000`.
It checks stock dimensions/format, space, resource hashes, unchanged entries,
boot/application and settings. The custom-image wrapper then adds the exact
build_04 application, retaining all its behaviour. It does not patch an unknown
application or accept alternate firmware by changing a hash constant.
Review `build/my-camera/manifest.json`, previews and the image's new SHA-256.
Budget is **1,056,768 bytes** in the tail; our public reference uses **775,424 bytes**.
Simpler images or lower JPEG quality can reduce size, but recheck frame transparency.

For graphics only on the original application, append `--stock-app` to
`build_custom_image.py`. That keeps all stock effects/frames and its SD-dependent
USB mode. The seven-frame-only controls, new dithering and no-card USB patch are
application features, not artwork-file properties.

## 5. Flash your custom graphics

`mk1_artwork_flash.py` is the repository's reusable **graphics-only** adaptation.
It has offline/simulated safety coverage; this entry point has not been exercised
on hardware. Its native resource whitelist extension is separate from the
original, default application-only whitelist. An AI agent should review both
before its first real use. The historical scripts above remain the recorded
path used for our first installation, with the repository’s reference hashes now
updated for blurred public artwork.

The application in `--image` must equal the application in `--known` byte for byte.
Thus use the public build_04 as `--known` only if that exact public image is what
you installed. For the owner’s earlier private installation, use the retained
private original image as `--known`; do not publish that file. The full backup
will reject the blurred public template as a preimage of the old private artwork.
For a stock-app graphics build, use the immutable stock dump. On later updates,
use the **exact previous custom full image**, which you must retain, as `--known`.

```bash
python3 tools/mk1_artwork_flash.py plan \
  --known analysis/build_04/image.bin --image build/my-camera/image.bin
python3 tools/mk1_artwork_flash.py backup \
  --known analysis/build_04/image.bin --output runs/custom-before
```

The backup independently reads all flash twice and checks the known image outside
settings. Review the plan and candidate hash. Ask the owner for approval for
**relocated**, then **inplace**, then **directory**, one phase at a time. Substitute
the reviewed target hash for `TARGET_SHA256` below; repeat with a new output folder
and the next phase only after its separate approval. Keep the **original --known**
argument unchanged across the three phases; the updater computes preceding stages.

```bash
# Only after explicit human approval for this phase and exact image:
python3 tools/mk1_artwork_flash.py install \
  --known analysis/build_04/image.bin --image build/my-camera/image.bin \
  --phase relocated --approved-phase --approve-sha256 TARGET_SHA256 \
  --output runs/custom-relocated
# Next approved command: --phase inplace --output runs/custom-inplace
# Final approved command: --phase directory --output runs/custom-directory
```

Empty phases write nothing; they still verify the current stage. Each nonempty
phase obtains two fresh full reads, accepts only expected preceding bytes outside
settings, carries the current live settings through, and changes only selected
4 KiB resource sectors. Source RAM is verified before erase, erase is checked,
each programmed sector is read twice, and all 4 MiB are compared afterward.
The directory phase report must say `matches_target_with_live_settings: true`.

Read-only comparison, then restart and check the graphics physically:

```bash
python3 tools/mk1_artwork_flash.py verify \
  --known build/my-camera/image.bin --output runs/custom-verify
```

This verifier compares the complete image except the live settings sector. The
exact-build boot verifier in section 3 does not accept a different artwork image;
use the generic full-flash verifier and the recorded application/USB tests instead.

To restore earlier **graphics with the same app**, reverse target/known and obtain
approval for each phase again. This cannot uninstall the effects application.
There is no universal MK1 full-firmware restore command in this folder; restoring
the application requires a reviewed transition or direct SPI recovery.

## Failures, timing and evidence

Never erase the chip or boot/header sectors; never overwrite live settings.
Never use `DestBin.bin` on the SD card: that stock updater can erase the image's
header/settings. No automatic retries. A failed writing phase means **keep power
and USB connected, retain every run directory and stop for diagnosis**. A new run
against the original preimage will correctly reject a partial installation.
Do not blindly use the historical `--resume-state` option; it needs a manually
reviewed partial-image transition. Do not power-cycle an uncertain operation.

Our native SPI program time for 11 application sectors was ~0.14 seconds total;
reliable source uploads took ~5 seconds. Complete independent backups and final
reads dominate elapsed time (the backup pair was ~87 seconds). A one-sector label
update with a full backup and full final read took ~104 seconds. Our original 242-sector art
install costs much more verification traffic; no fixed duration is promised.
4096-byte multi-packet uploads once lost seven packets; detection stopped before
flash. Use **512 bytes**, not the superficially faster failed mode.

Keep `journal.jsonl`, reports, both before reads, full read-back, manifests and
candidate hashes with the camera's records. Private backups/runs and generated
images are Git-ignored; do not publish personal SD contents or captured RAM by
accident. The reference stock dump stays untouched.
