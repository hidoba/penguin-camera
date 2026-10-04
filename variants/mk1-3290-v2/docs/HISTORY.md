# Work completed and evidence (2026-10-03/04)

The original work directory was a separate local MK1 project. This folder is a
portable handoff, not a copy of its large RAM dumps and private per-camera backups.
The included JSON installation summary records their original relative evidence
paths for provenance; those run directories are not included here.

## Reference and USB backup

Two independent Pico chip reads matched the immutable stock SHA-256 in README.md.
Read-only USB 0xCD access then matched USB, SPI, cache and allocator helpers against
that dump. A complete 8 MiB running-RAM dump was also captured for analysis.
Two independent successful USB flash reads matched each other and differed from
Pico only in live settings, which were preserved.

After artwork installation, the paired pre-effects USB backup hash was
`34bf5343f31ae8414b9f5cc7c44df2e9736e3640c47c7781228ff0bf5ad074ff`.
It equalled build_01 except settings bytes: flash `0x2fd088` stock 2 → live 3,
`0x2fd08c` stock 1 → live 0. These are observations of this unit, not settings to
force onto someone else's camera.

## Artwork

Seven frames, menu/background/button graphics and welcome/goodbye screens were
converted from the owner's supplied artwork. Frame alpha was translated to the
stock black-luma key and checked after JPEG decoding. Larger resources were
relocated into the erased tail; in-place updates and resource directory followed.
The original resource image SHA-256 was
`1c330d0a92d3242d67c82ca683b828b57fb1b7164de26c37a526bfea88db4e8c`.
242 changed sectors were installed with journals, per-sector verification and
complete read-backs. Boot/application/settings were preserved in that stage.

## Effects and UI

The person pictogram was traced to a pencil/sketch renderer, not portrait detection;
there were monochrome and colour forms depending on printing mode. That renderer
and kaleidoscope/colour filters were retired and their UI positions replaced with
three requested dithering modes. Penguin grayscale, tone compensation and date
rendering were ported to the MK1 ABI and 8 MiB bounds.

The owner tested a temporary live RAM version and said it was good except that the
upper information bar should stay original. Build_03 moved the label below it.
The next request changed its background to transparent while keeping white text:
build_04 changes only byte `0x7b094`, palette 250 → 249, relative to build_03.
That final update rewrote only sector `0x7b000`, with a fresh complete backup and
full verified read-back. All other bytes were preserved.

Eleven application sectors were initially installed as five payload and six
activation sectors; USB selector was last. The final transparent-label candidate
and actual live-settings full-image hashes are in README.md. The live hash differs
from the candidate because this camera's settings were preserved.

## Speed work and failed experiments

The bottleneck was USB command/verification overhead, rather than SPI program time.
Resident read/write routines reduced repeated bridge uploads, and source uploads
were consolidated into reliable single 512-byte packets. Eleven sectors' program
calls took ~0.139 s, source uploads ~4.954 s; complete paired backups took ~87 s.
The final one-sector label transaction took ~104.408 s including full reads.

A paced 4096-byte mode passed 16 benchmark trials, but later lost the first seven
of eight packets in a command (3,566 differing bytes in one checked region).
RAM comparison stopped that attempt **before erase/program**. Its allocations and
evidence were retained until the owner restarted; the eventual successful install
used 512-byte packets. Passing an upload benchmark is not sufficient proof that
larger packets are safe.

Earlier interrupted RAM-only sessions prompted owner restarts. A temporary
USB-disconnect override used mode 3 (camera) instead of mode 1 (shutdown) for live
RAM testing. It was never included in the persistent firmware. Stock USB unplug
shutdown remains. The boot check verifies this test override is absent.

## Proven and pending

Proven: matching stock chip reads; stock USB RAM/callback access; guarded native
SPI reads/programming; paired USB backups; resource and app sector installs with
full verification; native pixel/tone/date rehearsal; offline ROM-parser positive
and forced-CRC negative controls; temporary user feedback.

Pending at handoff: cold restart of the final installed build, storage enumeration
and vendor access without an SD card, and a complete physical capture/print/date
matrix. The no-card selector instruction is present and tested offline, but the
attempted no-card temporary RAM transition returned to webcam mode after a power
transition; that did not prove persistent behaviour. The summary correctly retains
`restart_verified: false` and `USB_without_SD_verified: false`.

## Repository additions

The tested local sources, privacy-edited prepared/source artwork, immutable dump, tone model and
trimmed Penguin ports are included. Host-specific imports were replaced with
relative package imports. The old upload CLI now exposes only the reliable 512-byte
choice. Original test dependence on a private backup was replaced with a synthetic
fixture that still exercises corruption-before-erase rejection.

Added offline wrappers reproduce the current public reference images and create custom
artwork images with either the stock or exact build_04 app. Added generic
`mk1_artwork_flash.py` supports changed-resource phases, explicit image-hash approval,
paired preimage checks, live settings preservation and directory-last publication.
Its resource-only native scope is explicit; the default application scope remains
restricted. This new entry point was tested offline and with simulated sessions,
**not used on hardware** while preparing this handoff. It should not be labelled
as a previously successful real-camera install.

## Public artwork privacy revision

Before publishing the final replacement commit, children’s faces in editable frame
007 and splash screens 048/049 were strongly blurred using the imagegen image-editing
skill. The corresponding prepared JPEGs, overview and conversion report were
regenerated. All other prepared resources are byte-identical to the original
inputs. Original frame transparency checks still pass (zero hidden-artwork or
visible-background pixels). The stock dump and native application are unchanged.

Public resource SHA-256: `c1dd84025bc7ea79e64bfb1e8f83ca028d63996f071b7e4682e29f380c605ec0`.
Public complete-image SHA-256: `c50df9e03f36f38ba19f4f909f4eb350ab0ce913dbc23ff52ca96b32bd2216c3`.
The public image uses 240 resource sectors (190 relocated, 49 in-place, one
directory), and 775,424 bytes in the free tail. The earlier 242-sector counts and
installed hashes above document the real private-camera work, not this privacy
revision. Full hashes stay strict: these public references replace the old
resource/build hashes, rather than disabling the gates.

The images are deliberately different from the ones installed on the owner’s
camera. Existing private installations need their retained exact old image as
the known preimage for later artwork changes. The privacy revision was verified
offline only; no camera RAM or flash was changed for it. The latest repository
commit was replaced rather than adding an original-photo commit to normal history.
Private originals remain outside the published repository.
