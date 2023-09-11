# coding: utf-8

import audioop
import csv
import json
import operator
import os
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

import miniaudio
import regex
import wx
import wx.lib.sized_controls as sc

from gui_components import (
    ColumnDefn,
    ImmutableObjectListView,
    SimpleDialog,
    make_sized_static_box,
)

DEFAULT_TITLE = "TTS Dataset Editor"
EDITED_METADATA_FILENAME = "metadata.edited.json"
SAVE_SOUND = os.fspath(Path.cwd().joinpath("sounds", "save.wav"))
ALERT_SOUND = os.fspath(Path.cwd().joinpath("sounds", "alert.wav"))
NAV_SOUND = os.fspath(Path.cwd().joinpath("sounds", "nav.wav"))

@dataclass
class WavAndTranscript:
    idx: int
    wavpath: Path
    transcript: str
    pending_review: bool = False
    deleted: bool = False

    def edit_transcript(self, value):
        if value != self.transcript:
            self.transcript = value
            return True
        return False

    def asdict(self):
        return {
            "idx": self.idx,
            "filename": self.wavpath.stem,
            "text": self.transcript,
            "pending_review": self.pending_review,
            "deleted": self.deleted,
        }

    @property
    def label(self):
        label = f"{self.idx + 1}. {self.wavpath.stem}"
        if self.pending_review:
            label = "(review) " + label
        if self.deleted:
            label = "(deleted) " + label
        return label

    @property
    def duration(self):
        return "-"

    def get_duration(self):
        with wave.open(os.fspath(self.wavpath)) as wavfile:
            return wavfile.getnframes() / wavfile.getframerate()


class SearchWindow(SimpleDialog):
    
    def addControls(self, parent):
        parent.SetSizerType("vertical")
        wx.StaticText(parent, -1, "Filter by:")
        self.textCtrl = wx.TextCtrl(parent, -1, style=wx.TE_RICH2)
        self.regexCheckbox = wx.CheckBox(parent, -1, "Regular expression")

    @classmethod
    def ShowModal(cls, parent):
        dlg = cls(parent=parent, title="Search")
        ret = wx.Dialog.ShowModal(dlg)
        if ret == wx.ID_CANCEL:
            return "", False
        return dlg.textCtrl.GetValue(), dlg.regexCheckbox.GetValue()


class MainWindow(SimpleDialog):
    def __init__(self):
        self._volume = 75
        self._autoplay_audio = False
        super().__init__(parent=None, title=DEFAULT_TITLE)
        self.SetSize(1000, 750)
        self.CenterOnScreen()
        self._set_accelerators()
        self.Bind(wx.EVT_CLOSE, self.onDClose, self)
        self._objects = None
        self._dataset_dir = None
        self._is_source_csv = False
        self._playback_device = miniaudio.PlaybackDevice()

    def addControls(self, parent):
        parent.SetSizerType("vertical")
        filterBox = make_sized_static_box(parent, "Filters")
        filterBox.SetSizerType("horizontal")
        self.searchBtn = wx.ToggleButton(filterBox, wx.ID_FIND, "Search")
        self.pendingReviewBtn = wx.ToggleButton(filterBox, -1, "Pending review")
        self.deletedBtn = wx.ToggleButton(filterBox, -1, "Deleted")
        wx.StaticText(parent, -1, "&Wavs")
        self.wavList = ImmutableObjectListView(
            parent,
            -1,
            columns=[
                ColumnDefn("File name", "left", 60, operator.attrgetter("label")),
                ColumnDefn("Duration", "center", 40, operator.attrgetter("duration")),
            ],
        )
        self.wavList.SetSizerProps(expand=True)
        wx.StaticText(parent, -1, "&Text utterance")
        self.transcriptTextCtrl = wx.TextCtrl(
            parent, -1, style=wx.TE_PROCESS_ENTER | wx.TE_RICH2
        )
        self.transcriptTextCtrl.SetSizerProps(expand=True)
        optionsBox = make_sized_static_box(parent, "Options")
        wx.StaticText(optionsBox, -1, "&Volume")
        volumeSlider = wx.Slider(optionsBox, -1, self._volume, 0, 100)
        autoplayCheckbox = wx.CheckBox(optionsBox, -1, "Auto play audio when navigating")
        autoplayCheckbox.SetValue(self._autoplay_audio)
        rtlCheckbox = wx.CheckBox(optionsBox, -1, "Right to left")
        buttonPanel = sc.SizedPanel(parent, -1)
        buttonPanel.SetSizerType("horizontal")
        self.openBtn = wx.Button(buttonPanel, wx.ID_OPEN, "&Open dataset directory")
        self.saveBtn = wx.Button(buttonPanel, wx.ID_SAVE, "&Save")
        self.exportCSVBtn = wx.Button(buttonPanel, wx.ID_SAVEAS, "&Export to CSV")
        self.closeDatasetBtn = wx.Button(buttonPanel, wx.ID_FILE1, "&Close dataset")
        self.Bind(wx.EVT_TOGGLEBUTTON, self.onFilterSearch, id=wx.ID_FIND)
        self.Bind(wx.EVT_TOGGLEBUTTON, self.onFilterPendingReview, self.pendingReviewBtn)
        self.Bind(wx.EVT_TOGGLEBUTTON, self.onFilterDeleted, self.deletedBtn)
        self.Bind(wx.EVT_BUTTON, self.onOpen, self.openBtn)
        self.Bind(wx.EVT_BUTTON, self.onSave, self.saveBtn)
        self.Bind(wx.EVT_BUTTON, self.onExportCSV, self.exportCSVBtn)
        self.Bind(wx.EVT_BUTTON, self.onCloseDataset, self.closeDatasetBtn)
        self.Bind(wx.EVT_LIST_ITEM_FOCUSED, self.onWavSelected, self.wavList)
        self.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.onPlayPauseWav, self.wavList)
        self.Bind(wx.EVT_TEXT_ENTER, self.onPlayPauseWav, self.transcriptTextCtrl)
        self.Bind(wx.EVT_TEXT, self.onTextChanged, self.transcriptTextCtrl)
        self.Bind(wx.EVT_SLIDER, self.onVolumeSlider, volumeSlider)
        self.Bind(
            wx.EVT_SET_FOCUS,
            lambda e: self.transcriptTextCtrl.SetInsertionPoint(0),
            self.transcriptTextCtrl,
        )
        rtlCheckbox.Bind(
            wx.EVT_CHECKBOX,
            lambda e: self.set_text_direction(e.IsChecked()),
            rtlCheckbox,
        )
        autoplayCheckbox.Bind(
            wx.EVT_CHECKBOX,
            lambda e: setattr(self, "_autoplay_audio", e.IsChecked()),
            autoplayCheckbox,
        )
        self.searchBtn.Enable(False)
        self.pendingReviewBtn.Enable(False)
        self.deletedBtn.Enable(False)
        self.saveBtn.Enable(False)
        self.exportCSVBtn.Enable(False)
        self.closeDatasetBtn.Enable(False)

    def getButtons(self, parent):
        return

    def _set_accelerators(self):
        accelerator_map = {
            "F1": wx.ID_HELP,
            "Ctrl+g": wx.ID_FIND,
            "Ctrl+o": wx.ID_OPEN,
            "Ctrl+s": wx.ID_APPLY,
            "Ctrl+w": wx.ID_FILE1,
            "Ctrl+r": wx.ID_EDIT,
            "Ctrl+d": wx.ID_DELETE,
            "Alt+right": wx.ID_FORWARD,
            "Alt+left": wx.ID_BACKWARD,
        }
        entries = []
        for shortcut, cmd_id in accelerator_map.items():
            accel = wx.AcceleratorEntry(cmd=cmd_id)
            accel.FromString(shortcut)
            entries.append(accel)
        self.SetAcceleratorTable(wx.AcceleratorTable(entries))
        # Bind custom IDs
        self.Bind(wx.EVT_MENU, self.onFilterSearch, id=wx.ID_FIND)
        self.Bind(wx.EVT_MENU, self.onHelp, id=wx.ID_HELP)
        self.Bind(wx.EVT_MENU, self.onNextItem, id=wx.ID_FORWARD)
        self.Bind(wx.EVT_MENU, self.onPrevItem, id=wx.ID_BACKWARD)
        self.Bind(wx.EVT_MENU, lambda e: self.save(True), id=wx.ID_APPLY)
        self.Bind(wx.EVT_MENU, self.onEdit, id=wx.ID_EDIT)
        self.Bind(wx.EVT_MENU, self.onDelete, id=wx.ID_DELETE)

    def set_text_direction(self, rtl=False):
        self.transcriptTextCtrl.SetLayoutDirection(
            wx.Layout_LeftToRight if not rtl else wx.Layout_RightToLeft
        )
        style = self.transcriptTextCtrl.GetDefaultStyle()
        style.SetAlignment(wx.TEXT_ALIGNMENT_RIGHT if rtl else wx.TEXT_ALIGNMENT_LEFT)
        self.transcriptTextCtrl.SetDefaultStyle(style)
        self.transcriptTextCtrl.SetValue(self.transcriptTextCtrl.GetValue())

    def onDClose(self, event):
        self._playback_device.stop()
        if self.has_unsaved_edits():
            retval = wx.MessageBox(
                "You have some unsaved edits.\nWould you like to save them before exiting?",
                "Warning",
                style=wx.YES_NO | wx.ICON_EXCLAMATION,
            )
            if retval == wx.YES:
                self.save(True)
        self._playback_device.close()
        sys.exit(0)

    def onOpen(self, event):
        chosen_dir = wx.DirSelector(
            "Choose TTS dataset directory", os.fspath(Path.home()), parent=self
        )
        if not chosen_dir:
            return
        elif not os.path.exists(chosen_dir):
            return wx.MessageBox(
                f"Directory not found", "Error", style=wx.OK | wx.ICON_ERROR
            )
        chosen_path = Path(chosen_dir)
        metadata_file = chosen_path.joinpath("metadata.csv")
        if not metadata_file.is_file():
            return wx.MessageBox(
                f"`metadata.csv` not found in {chosen_path}.",
                "No `metadata.csv`",
                style=wx.OK | wx.ICON_ERROR,
            )
        edited_metadata = chosen_path.joinpath(EDITED_METADATA_FILENAME)
        if edited_metadata.is_file():
            retval = wx.MessageBox(
                "You have saved edits in this directory.\nWould you like to reload  them?",
                "Previous edits found",
                style=wx.YES_NO | wx.ICON_EXCLAMATION,
            )
            if retval == wx.YES:
                metadata_file = edited_metadata

        objs = []
        if metadata_file.suffix == ".json":
            self._is_source_csv = False
            with open(metadata_file, "r", encoding="utf-8") as json_file:
                for entry in json.load(json_file):
                    wavewpath = chosen_path.joinpath("wavs", entry["filename"] + ".wav")
                    obj = WavAndTranscript(
                        idx=entry["idx"],
                        wavpath=wavewpath,
                        transcript=entry["text"],
                        pending_review=entry.get("pending_review", False),
                        deleted=entry.get("deleted", False),
                    )
                    objs.append(obj)
        else:
            self._is_source_csv = True
            with open(metadata_file, "r", encoding="utf-8") as csv_file:
                for (idx, row) in enumerate(csv.reader(csv_file, delimiter="|")):
                    if not row:
                        continue
                    wavewpath = chosen_path.joinpath("wavs", f"{row[0]}.wav")
                    objs.append(WavAndTranscript(idx, wavewpath, row[-1]))

        objs.sort(key=operator.attrgetter("idx"))
        self._objects = objs
        self._dataset_dir = chosen_path
        self.wavList.set_objects(self._objects, set_focus=True)
        history_file = self._dataset_dir.joinpath(".last_idx")
        try:
            idx_to_select = int(history_file.read_text())
        except (FileNotFoundError, ValueError):
            pass
        else:
            self.wavList.set_focused_item(idx_to_select)
        self.openBtn.Enable(False)
        self.searchBtn.Enable(True)
        self.pendingReviewBtn.Enable(True)
        self.deletedBtn.Enable(True)
        self.saveBtn.Enable(True)
        self.exportCSVBtn.Enable(not self._is_source_csv)
        self.closeDatasetBtn.Enable(True)
        self.set_title()

    def onSave(self, event):
        if self.save():
            wx.MessageBox(
                f"Edits saved to `{edited_metadata}`.", "Success", style=wx.ICON_INFORMATION
            )

    def onExportCSV(self, event):
        self.save()
        has_pending_review = any(filter(operator.attrgetter("pending_review"), self._objects))
        if has_pending_review:
            retval = wx.MessageBox(
                "Some items are marked as pending review. Would you like to export them without further changes?",
                "Pending review",
                style=wx.YES_NO|wx.ICON_WARNING
            )
            if retval == wx.NO:
                return
        rows = (
            (obj.wavpath.stem, "", obj.transcript.strip())
            for obj in self._objects
            if not obj.deleted
        )
        csv_filename = self._dataset_dir.joinpath(EDITED_METADATA_FILENAME).with_suffix(".csv")
        with open(csv_filename, "w", encoding="utf-8", newline="\n") as file:
            writer = csv.writer(file, delimiter="|")
            writer.writerows(rows)
        wx.MessageBox(
            f"Data exported to `{csv_filename}`",
            "Success",
            style=wx.ICON_INFORMATION
        )

    def onCloseDataset(self, event):
        self._playback_device.stop()
        if self.has_unsaved_edits():
            retval = wx.MessageBox(
                "You have some unsaved edits.\nWould you like to save them?",
                "Warning",
                style=wx.YES_NO | wx.ICON_EXCLAMATION,
            )
            if retval == wx.YES:
                self.save(True)
        self.transcriptTextCtrl.SetValue("")
        self.openBtn.Enable(True)
        self.searchBtn.Enable(False)
        self.pendingReviewBtn.Enable(False)
        self.deletedBtn.Enable(False)
        self.saveBtn.Enable(False)
        self.exportCSVBtn.Enable(False)
        self.closeDatasetBtn.Enable(False)
        self._objects = None
        self._dataset_dir = None
        self.wavList.set_objects([], set_focus=True)
        self.set_title()

    def onWavSelected(self, event):
        utterance = self.wavList.get_selected()
        if utterance is None:
            return
        self.transcriptTextCtrl.SetValue(utterance.transcript)
        if utterance.pending_review or utterance.deleted:
                self.play_file(ALERT_SOUND)

    def onTextChanged(self, event):
        utterance = self.wavList.get_selected()
        if utterance is None:
            return
        has_changes = utterance.edit_transcript(event.GetString())
        if has_changes:
            self.set_title(dirty=True)

    def has_unsaved_edits(self):
        return self.GetTitle().startswith("*")

    def onPlayPauseWav(self, event):
        utterance = self.wavList.get_selected()
        if utterance is None:
            return wx.Bell()
        wav_file = os.fspath(utterance.wavpath)
        self.play_file(wav_file)

    def onNextItem(self, event):
        selected_idx = self.wavList.GetFocusedItem()
        if selected_idx == wx.NOT_FOUND:
            return wx.Bell()
        next_item_idx = self.wavList.GetNextItem(
            selected_idx, geometry=wx.LIST_NEXT_BELOW
        )
        if next_item_idx == wx.NOT_FOUND:
            return wx.Bell()
        self.wavList.set_focused_item(next_item_idx, sel_only=True)
        if self._autoplay_audio:
            self.onPlayPauseWav(None)
        else:
            self.play_file(NAV_SOUND)

    def onPrevItem(self, event):
        selected_idx = self.wavList.GetFocusedItem()
        if selected_idx == wx.NOT_FOUND:
            return wx.Bell()
        prev_item_idx = self.wavList.GetNextItem(
            selected_idx, geometry=wx.LIST_NEXT_ABOVE
        )
        if prev_item_idx == wx.NOT_FOUND:
            return wx.Bell()
        self.wavList.set_focused_item(prev_item_idx, sel_only=True)
        if self._autoplay_audio:
            self.onPlayPauseWav(None)
        else:
            self.play_file(NAV_SOUND)

    def onVolumeSlider(self, event):
        self._volume = event.GetInt()

    def onEdit(self, event):
        utterance = self.wavList.get_selected()
        if utterance is not None:
            utterance.pending_review = not utterance.pending_review
            if utterance.pending_review:
                self.play_file(ALERT_SOUND)
            self.wavList.SetItemText(self.wavList.GetFocusedItem(), utterance.label)
            self.set_title(dirty=True)
        return wx.Bell()

    def onDelete(self, event):
        utterance = self.wavList.get_selected()
        if utterance is not None:
            utterance.deleted = not utterance.deleted
            if utterance.deleted:
                self.play_file(ALERT_SOUND)
            self.wavList.SetItemText(self.wavList.GetFocusedItem(), utterance.label)
            self.set_title(dirty=True)
        return wx.Bell()

    def onFilterSearch(self, event):
        if self.deletedBtn.GetValue():
            self.deletedBtn.SetValue(False)
        if self.pendingReviewBtn.GetValue():
            self.pendingReviewBtn.SetValue(False)
        if self.searchBtn.GetValue():
            search_string, is_regex = SearchWindow.ShowModal(parent=self)
            if not search_string:
                self.searchBtn.SetValue(False)
                wx.Bell()
                return
            if is_regex:
                try:
                    reg = regex.compile(search_string, regex.U)
                except:
                    self.searchBtn.SetValue(False)
                    wx.MessageBox("Invalid regex", "Error", style=wx.ICON_ERROR)
                    return
                objs = list(filter(
                    lambda o: reg.match(o.transcript),
                    self.wavList._objects
                ))
            else:
                objs = list(filter(
                    lambda o: search_string in o.transcript,
                    self.wavList._objects
                ))
            if objs:
                self.wavList.set_objects(objs)
            else:
                wx.MessageBox("No matches found.", "No matches", style=wx.ICON_WARNING)
                self.searchBtn.SetValue(False)
        else:
            self.wavList.set_objects(self._objects)

    def onFilterPendingReview(self, event):
        if self.searchBtn.GetValue():
            self.searchBtn.SetValue(False)
        if self.deletedBtn.GetValue():
            self.deletedBtn.SetValue(False)
        if self.pendingReviewBtn.GetValue():
            filtered_objs = filter(
                operator.attrgetter("pending_review"),
                self._objects
            )
            objs = list(filtered_objs)
        else:
            objs = self._objects
        self.wavList.set_objects(objs, set_focus=True)

    def onFilterDeleted(self, event):
        if self.searchBtn.GetValue():
            self.searchBtn.SetValue(False)
        if self.pendingReviewBtn.GetValue():
            self.pendingReviewBtn.SetValue(False)
        if self.deletedBtn.GetValue():
            filtered_objs = filter(
                operator.attrgetter("deleted"),
                self._objects
            )
            objs = list(filtered_objs)
        else:
            objs = self._objects
        self.wavList.set_objects(objs, set_focus=True)

    def onHelp(self, event):
        hotkeys = "\n".join([
            "Ctrl + O: open dataset directory",
            "Ctrl + W: close currently opened dataset",
            "Ctrl + S: save edits",
            "Ctrl + D: mark as deleted",
            "Ctrl + R: mark as pending review",
            "Alt + right arrow: next utterance",
            "Alt + left arrow: previous utterance",
            "Enter: play current utterance's audio",
        ])
        wx.MessageBox(
            hotkeys,
            "Hotkeys",
            style=wx.ICON_INFORMATION
        )

    def play_file(self, filename):
        self._playback_device.stop()
        file_stream = miniaudio.stream_file(filename)
        stream_with_volume = miniaudio.stream_with_callbacks(
            file_stream,
            frame_process_method=lambda audio: audioop.mul(
                audio, 2, self._volume / 100.0
            ),
        )
        # workaround of miniaudio issue
        next(stream_with_volume)
        self._playback_device.start(stream_with_volume)

    def save(self, play_sound=False):
        if not self.has_unsaved_edits():
            wx.Bell()
            return False
        edited_metadata = self._dataset_dir.joinpath(EDITED_METADATA_FILENAME)
        if self._is_source_csv and edited_metadata.exists():
            retval = wx.MessageBox(
                f"You have existing edits in the file `{edited_metadata}`.\nSaving will overwrite those edits.\nAre you sure you want to proceed?",
                "Possible data loss",
                style=wx.YES_NO|wx.ICON_ERROR
            )
            if retval == wx.NO:
                return False
        entries = [
            obj.asdict()
            for obj in self._objects
        ]
        entries.sort(key=operator.itemgetter("idx"))
        with open(edited_metadata, "w", encoding="utf-8") as json_file:
            json.dump(entries, json_file, ensure_ascii=False, indent=2)
        self.set_title()
        if play_sound:
            self.play_file(SAVE_SOUND)
        selected_idx = self.wavList.GetFocusedItem()
        if selected_idx != wx.NOT_FOUND:
            history_file = self._dataset_dir.joinpath(".last_idx")
            history_file.write_text(str(selected_idx))
        return True

    def set_title(self, dirty=False):
        title = DEFAULT_TITLE
        if self._dataset_dir:
            title = self._dataset_dir.name + " - " + title
        if dirty:
            title = "* " + title
        self.SetTitle(title)


if __name__ == "__main__":
    wx.Log.EnableLogging()
    app = wx.App()
    main_win = MainWindow()
    main_win.ShowModal()
    app.MainLoop()
    sys.exit()
