"""Adobe Audition integration.

Writes a native multitrack session (.sesx, Audition CC 2017+) next to the exported WAVs so a
SIB project opens in Audition with every track, clip position, volume, pan, mute, tempo and
marker in place. Structure follows the .sesx XML layout used by Audition CC (see README credits).
"""
from __future__ import annotations

import glob
import html
import os
import subprocess
import hashlib
import numpy as np

from .audio_io import save_audio
from . import dsp

GUID = dict(
    track_fader="f822a180-4604-47ca-850f-e9f58477dfff",
    track_mute="d51c1020-8741-4fcc-872a-7adb3074880d",
    track_pan="b5d53c02-08c3-41e7-b9e3-2015248cf80d",
    clip_fader="673cd4d7-8c21-4528-ad40-e541ff6dcd70",
    clip_mute="29e428ef-8d16-4ee2-a01a-ada26356009f",
    clip_pan="80da3284-8a9e-4165-a773-f372a4d619bc",
)


def _guid(s):
    h = hashlib.md5(s.encode()).hexdigest()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _fmt(v):
    v = float(v)
    return str(int(v)) if v.is_integer() else f"{v:.6f}".rstrip("0").rstrip(".")


def _clip_xml(cid, file_id, name, start, length, src_in, gain_lin, fade_in, fade_out):
    end = start + length
    return f'''        <audioClip clipAutoCrossfade="true" crossFadeHeadClipID="-1" crossFadeTailClipID="-1" endPoint="{end}" fileID="{file_id}" hue="-1" id="{cid}" lockedInTime="false" looped="false" name="{html.escape(name)}" offline="false" select="false" sourceInPoint="{src_in}" sourceOutPoint="{src_in + length}" startPoint="{start}" zOrder="{cid}">
          <component componentGuid="{GUID['clip_fader']}" componentID="Audition.Fader" id="clipGain" name="volume" powered="true">
            <parameter index="0" name="volume" parameterValue="{_fmt(gain_lin)}"/>
            <parameter index="1" name="static gain" parameterValue="1"/>
          </component>
          <component componentGuid="{GUID['clip_mute']}" componentID="Audition.Mute" id="clipMute" name="Mute" powered="true">
            <parameter index="0" parameterValue="0"/>
            <parameter index="1" name="mute" parameterValue="0"/>
          </component>
          <component componentGuid="{GUID['clip_pan']}" componentID="Audition.StereoPanner" id="clipPan" name="StereoPanner" powered="true">
            <parameter index="0" name="Pan" parameterValue="0"/>
          </component>
          <fadeIn crossFadeLinkType="linkedAsymmetric" endPoint="{fade_in}" shape="19" startPoint="0" type="log"/>
          <fadeOut crossFadeLinkType="linkedAsymmetric" endPoint="{length}" shape="19" startPoint="{length - fade_out}" type="log"/>
          <channelMap><channel index="0" sourceIndex="0"/><channel index="1" sourceIndex="1"/></channelMap>
        </audioClip>'''


def _track_xml(idx, name, vol_lin, pan, mute, solo, clips_xml, hue):
    mv = 1 if mute else 0
    return f'''      <audioTrack automationLaneOpenState="false" id="{10000 + idx}" index="{idx}" select="false" visible="true">
        <trackParameters trackHeight="134" trackHue="{hue}" trackMinimized="false">
          <name>{html.escape(name)}</name>
        </trackParameters>
        <trackAudioParameters audioChannelType="stereo" automationMode="1" monitoring="false" recordArmed="false" solo="{'true' if solo else 'false'}" soloSafe="false">
          <trackOutput outputID="10000" type="trackID"/>
          <trackInput inputID="1"/>
          <component componentGuid="{GUID['track_fader']}" componentID="Audition.Fader" id="trackFader" name="volume" powered="true">
            <parameter index="0" name="volume" parameterValue="{vol_lin:.6f}"/>
            <parameter index="1" name="static gain" parameterValue="1"/>
          </component>
          <component componentGuid="{GUID['track_mute']}" componentID="Audition.Mute" id="trackMute" name="Mute" powered="true">
            <parameter index="0" parameterValue="{mv}"/>
            <parameter index="1" name="mute" parameterValue="{mv}"/>
          </component>
          <component componentGuid="{GUID['track_pan']}" componentID="Audition.StereoPanner" id="trackPan" name="StereoPanner" powered="true">
            <parameter index="0" name="Pan" parameterValue="0"/>
          </component>
        </trackAudioParameters>
{clips_xml}
      </audioTrack>'''


def _master_xml(index):
    return f'''      <masterTrack automationLaneOpenState="false" id="10000" index="{index}" select="false" visible="true">
        <trackParameters trackHeight="134" trackHue="-1" trackMinimized="false">
          <name>Mix</name>
        </trackParameters>
        <trackAudioParameters audioChannelType="stereo" automationMode="1" monitoring="false" recordArmed="false" solo="false" soloSafe="true">
          <trackOutput outputID="1" type="hardwareOutput"/>
          <trackInput inputID="-1"/>
          <component componentGuid="ab6aebff-3601-4db4-9933-781624df75f0" componentID="Audition.Fader" id="trackFader" name="volume" powered="true">
            <parameter index="0" name="volume" parameterValue="1"/>
            <parameter index="1" name="static gain" parameterValue="1"/>
          </component>
          <component componentGuid="44393786-9106-434d-abf4-d9760c2096e2" componentID="Audition.Mute" id="trackMute" name="Mute" powered="true">
            <parameter index="0" parameterValue="0"/>
            <parameter index="1" name="mute" parameterValue="0"/>
          </component>
          <component componentGuid="6fa88450-7b01-4b88-9ddd-3cb7930b5d51" componentID="Audition.StereoPanner" id="trackPan" name="StereoPanner" powered="true">
            <parameter index="0" name="Pan" parameterValue="0"/>
          </component>
        </trackAudioParameters>
      </masterTrack>'''


def _markers_xmp(markers, sr):
    if not markers:
        return ""
    items = []
    for i, (pos, name) in enumerate(markers):
        g = _guid(f"sib-marker-{i}-{pos}")
        items.append(f'''                        <rdf:li rdf:parseType="Resource">
                           <xmpDM:startTime>{int(pos)}</xmpDM:startTime>
                           <xmpDM:name>{html.escape(name)}</xmpDM:name>
                           <xmpDM:cuePointParams>
                              <rdf:Seq>
                                 <rdf:li rdf:parseType="Resource">
                                    <xmpDM:key>marker_guid</xmpDM:key>
                                    <xmpDM:value>xmp:id:{g}</xmpDM:value>
                                 </rdf:li>
                              </rdf:Seq>
                           </xmpDM:cuePointParams>
                           <xmpDM:guid>xmp:id:{g}</xmpDM:guid>
                        </rdf:li>''')
    return f'''    <xmpMetadata><![CDATA[<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="SIB Studio Mixer">
   <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
      <rdf:Description rdf:about=""
            xmlns:xmp="http://ns.adobe.com/xap/1.0/"
            xmlns:dc="http://purl.org/dc/elements/1.1/"
            xmlns:xmpDM="http://ns.adobe.com/xmp/1.0/DynamicMedia/">
         <xmp:CreatorTool>SIB Studio Mixer</xmp:CreatorTool>
         <dc:format>application/xml</dc:format>
         <xmpDM:Tracks>
            <rdf:Bag>
               <rdf:li rdf:parseType="Resource">
                  <xmpDM:trackName>CuePoint Markers</xmpDM:trackName>
                  <xmpDM:trackType>Cue</xmpDM:trackType>
                  <xmpDM:frameRate>f{sr}</xmpDM:frameRate>
                  <xmpDM:markers>
                     <rdf:Seq>
{chr(10).join(items)}
                     </rdf:Seq>
                  </xmpDM:markers>
               </rdf:li>
            </rdf:Bag>
         </xmpDM:Tracks>
      </rdf:Description>
   </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>]]></xmpMetadata>'''


def export_session(project, out_dir, mode="both", include_breath_markers=False, progress=None):
    """mode: 'processed' (FX printed), 'dry' (original clips, editable), 'both' (dry tracks muted).

    Returns path of the written .sesx file.
    """
    sr = project.sr
    media = os.path.join(out_dir, "Audio")
    os.makedirs(media, exist_ok=True)
    files = []      # (id, abs_path, rel_path)
    tracks_xml = []
    idx = 1
    duration = 1

    def add_file(path):
        fid = len(files)
        files.append((fid, os.path.abspath(path), os.path.relpath(path, out_dir)))
        return fid

    def safe(s):
        return "".join(ch if ch.isalnum() or ch in "-_ " else "_" for ch in s).strip() or "track"

    for t in project.tracks:
        hue = int(int(t.color.lstrip("#")[:2], 16) / 255 * 360) if t.color.startswith("#") else -1
        if mode in ("processed", "both"):
            if progress:
                progress(f"Rendering {t.name}")
            off, a = project.render_track(t)
            if len(a):
                gl, gr = dsp.pan_gains(t.pan)  # pan is baked into the printed file
                a = a * np.array([gl, gr], np.float32)
                p = os.path.join(media, f"{idx:02d}_{safe(t.name)}_FX.wav")
                save_audio(p, a, sr, 24)
                fid = add_file(p)
                cx = _clip_xml(0, fid, f"{t.name} (SIB FX)", off, len(a), 0, 1.0, 0, 0)
                tracks_xml.append(_track_xml(idx, f"{t.name} [SIB FX]", float(dsp.db_to_lin(t.volume_db)),
                                             t.pan, t.mute, t.solo, cx, hue))
                duration = max(duration, off + len(a))
                idx += 1
        if mode in ("dry", "both"):
            clips_xml = []
            used = {}
            for j, c in enumerate(sorted(t.clips, key=lambda c: c.start)):
                if c.file_id not in used:
                    src = project.pool.get(c.file_id)
                    p = os.path.join(media, f"{idx:02d}_{safe(t.name)}_{safe(c.name)}_{j}.wav")
                    save_audio(p, src, sr, 24)
                    used[c.file_id] = add_file(p)
                clips_xml.append(_clip_xml(j, used[c.file_id], c.name, c.start, c.length, c.src_in,
                                           float(dsp.db_to_lin(c.gain_db)), c.fade_in, c.fade_out))
                duration = max(duration, c.end)
            if clips_xml:
                dry_mute = t.mute or (mode == "both")
                tracks_xml.append(_track_xml(idx, f"{t.name}{' [dry]' if mode == 'both' else ''}",
                                             float(dsp.db_to_lin(t.volume_db)), t.pan, dry_mute, False,
                                             "\n".join(clips_xml), hue))
                idx += 1
    tracks_xml.append(_master_xml(idx))
    markers = [(m.pos, m.name) for m in project.markers]
    if include_breath_markers:
        for t in project.vocal_tracks():
            for r in t.breath_regions:
                markers.append((int(r[0]), f"Breath ({t.name})"))
    markers.sort()
    files_xml = "\n".join(
        f'    <file absolutePath="{html.escape(a)}" id="{i}" mediaHandler="AmioWav" relativePath="{html.escape(r)}"/>'
        for i, a, r in files)
    tfs = (f'      <timeFormatState beatsPerBar="{project.time_sig}" beatsPerMinute="{project.bpm!r}" '
           f'customFrameRate="12" linkToDefaultTimeSettings="false" noteLength="4" subdivisions="16" '
           f'timeCodeDropFrame="false" timeCodeFrameRate="30" timeCodeNTSC="false" timeFormat="timeFormatBarsBeats"/>')
    xmp = _markers_xmp(markers, sr)
    xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<!DOCTYPE sesx>
<sesx version="1.9">
  <session appBuild="SIB Studio Mixer" appVersion="25.0" audioChannelType="stereo" bitDepth="32" duration="{duration}" sampleRate="{sr}">
    <tracks>
{chr(10).join(tracks_xml)}
    </tracks>
    <sessionState ctiPosition="0" smpteStart="0">
{tfs}
      <mixingOptionState defaultPanModeLogarithmic="true" panPower="-3" playOverlappingClips="false"/>
    </sessionState>
{xmp + chr(10) if xmp else ''}  </session>
  <files>
{files_xml}
  </files>
</sesx>
'''
    path = os.path.join(out_dir, f"{safe(project.name)}.sesx")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(xml)
    # marker list as CSV too (Audition Markers panel can import it)
    with open(os.path.join(out_dir, f"{safe(project.name)}_markers.csv"), "w", encoding="utf-8") as fh:
        fh.write("Name\tStart\tDuration\tTime Format\tType\tDescription\n")
        for pos, name in markers:
            fh.write(f"{name}\t{_hms(pos / sr)}\t0:00.000\tdecimal\tCue\t\n")
    return path


def _hms(sec):
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:06.3f}"


def find_audition() -> str | None:
    pats = [r"C:\Program Files\Adobe\Adobe Audition*\Adobe Audition.exe",
            r"C:\Program Files (x86)\Adobe\Adobe Audition*\Adobe Audition.exe",
            r"C:\Program Files\Adobe\Adobe Audition*\Audition.exe"]
    hits = []
    for p in pats:
        hits += glob.glob(p)
    return sorted(hits)[-1] if hits else None


def open_in_audition(sesx_path):
    exe = find_audition()
    if exe:
        subprocess.Popen([exe, os.path.abspath(sesx_path)])
        return True
    if hasattr(os, "startfile"):
        os.startfile(os.path.abspath(sesx_path))  # type: ignore[attr-defined]
        return True
    return False
