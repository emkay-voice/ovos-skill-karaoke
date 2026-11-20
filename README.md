# <img src='icon.png' card_color='#ffcc33' width='50' height='50' style='vertical-align:bottom'/> Karaoke

Your OVOS sings - with its own voice, autotuned onto the melody, with a band
behind it. Or it plays the band and you sing: karaoke, with the words, the
chords and the melody on the screen.

Songs are ordinary MIDI-karaoke files (`.kar`). A few come with the skill,
more are in the online songbook, and your own `.kar` files are welcome.
Everything is made on the device in Python - the voice is the device's own
TTS, the band a small General MIDI synthesizer - so nothing is sent anywhere
and it works on any [OpenVoiceOS](https://openvoiceos.org): a Mark II, a
Raspberry Pi, a PC, Docker, or [Emkay](https://github.com/emkay-voice/emkay)
in a browser, where it was made.

[![Build](https://github.com/emkay-voice/ovos-skill-karaoke/actions/workflows/publish.yml/badge.svg)](https://github.com/emkay-voice/ovos-skill-karaoke/actions/workflows/publish.yml)
[![PyPI version](https://img.shields.io/pypi/v/ovos-skill-karaoke.svg)](https://pypi.org/project/ovos-skill-karaoke/)

## Usage
```
"sing Daisy Bell"                   the device sings
"karaoke Jingle Bells"              you sing
"start karaoke"
"what songs can you sing"
"find a song about christmas"       by title, kind or words of the song
"can you sing a song?"              it's shy - say "come on"
"stop"

"syng Hej Emkay"                    (Danish)
"find en sang om sommer"
"kan du synge en sang?"
```

Asked to sing just *a* song, it is a little shy at first - "Oh no, you'll
just laugh at me" - until you encourage it. Then: "I'm a bit nervous about
my voice, but here goes nothing", and afterwards "thank you for listening"
or "now you try" - lines from Mycroft's own singing skill, which the Mark 1
demo used to sing every two minutes.

The first time a song is asked for, it takes a few seconds to get ready (the
device says it is warming up its voice); after that it starts at once.

## The songs

| Song | | |
|---|---|---|
| Daisy Bell (Bicycle Built for Two) | waltz | ships with the skill |
| Hey Emkay | disco | ships with the skill, in 10 languages |
| Goodnight from Emkay | lullaby | ships with the skill |
| Emkay Rock | rock'n'roll | online songbook |
| Offline Island | reggae | online songbook |
| Jingle Bells | sleigh ride | online songbook |

*Daisy Bell* (Harry Dacre, 1892) was the first song a computer ever sang -
an IBM 7094 in 1961, the song HAL sings in *2001: A Space Odyssey*. *Jingle
Bells* is James Lord Pierpont's (1857). Both are in the public domain; the
Emkay songs are new, written for this songbook. All arrangements are the
songbook's own and free to use (CC0).

### In other languages

A song can come in several languages - `hey_emkay.kar`, `hey_emkay.da.kar`,
... - and they count as one song: the device sings the version in its own
language, or English when there is none. *Hey Emkay* is in English, Danish,
German, Spanish, French, Italian, Dutch, Portuguese, Catalan and Basque - the
languages Emkay speaks. They are singable rewrites rather than translations
(same melody, a syllable per note); corrections from native speakers are
very welcome.

### Your own songs

Put `.kar` (or `.mid` with lyrics) files in the skill's `songs` folder:

```
~/.local/share/mycroft/filesystem/skills/ovos-skill-karaoke.emkay-voice/songs/
```

or add folders to the `song_folders` setting. They are found by their title
(the `@T` line of a .kar file, else the file name), and your own come first.
Any karaoke file works: the skill finds the lyrics, works out which track is
the melody (a .kar file doesn't say) and which chords the band plays.

Please only use files you are allowed to use. Most `.kar` files on the
internet are of songs still under copyright, which is why none of those are
in the songbook.

### The online songbook

[`songbook/`](songbook/) in this repository: `index.json` lists the songs,
each a `.kar` file. The skill fetches the list when a song isn't on the
device, and keeps a song once it has been sung. Another songbook can be set
with `songbook_url`; an empty one keeps the skill offline.

The songbook's sources are the ABC files in [`tools/songs/`](tools/songs/)
(melody, chords and lyrics), arranged into `.kar` files by
`tools/arrange.py` - see [DEVELOPMENT.md](DEVELOPMENT.md) to add a song.

## How it sings

1. Every word of the lyrics is spoken on its own by the device's TTS - asked
   from ovos-audio over the bus (`speak:b64_audio`), or, where nothing
   answers that (Emkay), with the TTS plugin loaded in the skill. Spoken
   alone, a word's borders are certain whatever TTS it is, and it comes out
   clearly, as a singer would say it.
2. The syllables are found in the sound itself: a vowel is loud, voiced and
   has its energy where vowels do, so there is a hump for each one.
3. Each syllable goes on its notes: the vowel on the beat, the consonants
   just before and after. TD-PSOLA moves the pitch and stretches the
   vowels, with a little glide between notes and a light vibrato on long
   ones. The key is chosen so the melody sits where the voice is happy.
4. The band plays everything but the melody, in that key.

A voice reading another language's lyrics keeps its accent - a Danish voice
singing *Daisy Bell* is charming rather than correct.

## On a screen

- **Karaoke:** the line being sung - chords on top, the melody as a small
  piano roll, the words underneath lighting up as they are sung, the next
  line below. Drawn as SVG on the fly and shown with the stock
  `SYSTEM_ImageFrame` page, so any OVOS GUI shows it.
- **When the device sings:** the face, its mouth moving with the vowels
  (`enclosure.mouth.viseme_list`). The words of each line go on the bus as
  `ovos.karaoke.lyrics` (`title`, `line`, `next`, `mode`), and
  `ovos.karaoke.ended` when the song is over, so a GUI can show them under
  the face - Emkay does. With `sing_screen` set to `lyrics` the words are
  shown as in karaoke instead.

## Sound

The band is a General MIDI synthesizer in numpy: an instrument for each GM
family (piano, electric piano, clavinet, bells, organ, accordion, guitar,
bass, strings, brass, reeds, flutes, synth leads, pads) and a drum kit, with
a little room reverb. It needs nothing installed.

If the device has [FluidSynth](https://www.fluidsynth.org) and a General MIDI
SoundFont, the band uses those instead - real instrument samples:

```bash
sudo apt install fluidsynth fluid-soundfont-gm
```

The SoundFont is looked for where distributions install one, or set with
`soundfont`.

## Settings

| setting | default | |
|---|---|---|
| `songbook_url` | this repository's `songbook/` | the online songbook; empty = offline only |
| `song_folders` | `[]` | more folders with your own `.kar` files |
| `soundfont` | | a `.sf2` for FluidSynth, if it isn't found by itself |
| `sing_screen` | `face` | `face`: the face sings; `lyrics`: show the words |

## Playback

The song is played through OCP like any other music (`stop`, `pause` and the
volume work as usual), as one audio file the skill made. On Docker setups
the skill and ovos-audio must share the skill's data folder for the player
to find that file.

## Install
```bash
pip install ovos-skill-karaoke
```

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md).

## Searching

"find a song about christmas", "do you have a song about the moon", "find a
reggae song": the skill looks through the titles, the tags (`@Itags:` in a
.kar file, `tags` in the songbook) and the words of the songs, shows what it
found as tiles on the screen and asks which one to sing.

## Category
**Entertainment**

## Tags
#karaoke #singing #music #midi #fun
