# coding: utf-8

from __future__ import annotations

import contextlib
import threading
import time
import typing as t
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from functools import reduce
from itertools import chain
from typing import Optional

import wx
import wx.lib.mixins.listctrl as listmix
import wx.lib.sized_controls as sc

THREADED_WORKER = ThreadPoolExecutor()
ID_SKIP = 32000

# Some custom types
ObjectCollection = t.Iterable[t.Any]
LongRunningTask = t.Callable[[t.Any], t.Any]
DoneCallback = t.Callable[[Future], None]


def make_sized_static_box(parent, title):
    stbx = sc.SizedStaticBox(parent, -1, title)
    stbx.SetSizerProp("expand", True)
    stbx.Sizer.AddSpacer(25)
    return stbx


class EnhancedSpinCtrl(wx.SpinCtrl):
    """
    Select the content of the ctrl when focused to make editing more easier.
    Inspired by a similar code in NVDA's gui package.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.Bind(wx.EVT_SET_FOCUS, self.onFocus, self)

    def onFocus(self, event):
        event.Skip()
        length = len(str(self.GetValue()))
        self.SetSelection(0, length)


class DialogListCtrl(wx.ListCtrl, listmix.ListCtrlAutoWidthMixin):
    def __init__(
        self,
        parent,
        id,
        pos=wx.DefaultPosition,
        size=wx.DefaultSize,
        style=wx.BORDER_SUNKEN
        | wx.LC_SINGLE_SEL
        | wx.LC_REPORT
        | wx.LC_EDIT_LABELS
        | wx.LC_VRULES,
    ):
        wx.ListCtrl.__init__(self, parent, id, pos, size, style)
        listmix.ListCtrlAutoWidthMixin.__init__(self)

    def set_focused_item(self, idx: int, sel_only=False):
        if idx >= self.ItemCount:
            return
        self.EnsureVisible(idx)
        self.Select(idx)
        self.SetItemState(idx, wx.LIST_STATE_FOCUSED, wx.LIST_STATE_FOCUSED)
        self.SetItemState(idx, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
        if not sel_only:
            self.SetFocus()


class Dialog(wx.Dialog):
    """Base dialog for `Bookworm` GUI dialogs."""

    def __init__(self, parent, title, size=(450, 450), style=wx.DEFAULT_DIALOG_STYLE):
        super().__init__(parent, title=title, style=style)
        self.parent = parent

        panel = wx.Panel(self, -1, size=size)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.addControls(sizer, panel)
        line = wx.StaticLine(panel, -1, size=(20, -1), style=wx.LI_HORIZONTAL)
        sizer.Add(line, 0, wx.GROW | wx.RIGHT | wx.TOP, 10)
        buttonsSizer = self.getButtons(panel)
        if buttonsSizer:
            sizer.Add(buttonsSizer, 0, wx.ALIGN_CENTER | wx.ALL, 10)

        panel.SetSizer(sizer)
        panel.Layout()
        sizer.Fit(panel)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(panel, 2, wx.EXPAND | wx.ALL, 15)
        self.SetSizer(sizer)
        self.Fit()
        self.Center()

    def addControls(self, sizer):
        raise NotImplementedError

    def getButtons(self, parent):
        btnsizer = wx.StdDialogButtonSizer()
        # Translators: the label of the OK button in a dialog
        okBtn = wx.Button(parent, wx.ID_OK, "OK")
        okBtn.SetDefault()
        # Translators: the lable of the cancel button in a dialog
        cancelBtn = wx.Button(parent, wx.ID_CANCEL, "Cancel")
        for btn in (okBtn, cancelBtn):
            btnsizer.AddButton(btn)
        btnsizer.Realize()
        return btnsizer


class SimpleDialog(sc.SizedDialog):
    """Basic dialog for simple  GUI forms."""

    def __init__(self, parent, title, style=wx.DEFAULT_DIALOG_STYLE, **kwargs):
        super().__init__(parent, title=title, style=style, **kwargs)
        self.parent = parent

        panel = self.GetContentsPane()
        self.addControls(panel)
        buttonsSizer = self.getButtons(panel)
        if buttonsSizer is not None:
            self.SetButtonSizer(buttonsSizer)

        self.Layout()
        self.Fit()
        self.SetMinSize(self.GetSize())
        self.Center(wx.BOTH)

    def SetButtonSizer(self, sizer):
        bottomSizer = wx.BoxSizer(wx.VERTICAL)
        line = wx.StaticLine(self, -1, size=(20, -1), style=wx.LI_HORIZONTAL)
        bottomSizer.Add(line, 0, wx.TOP | wx.EXPAND, 15)
        bottomSizer.Add(sizer, 0, wx.EXPAND | wx.ALL, 10)
        super().SetButtonSizer(bottomSizer)

    def addControls(self, parent):
        raise NotImplementedError

    def getButtons(self, parent):
        btnsizer = wx.StdDialogButtonSizer()
        # Translators: the label of the OK button in a dialog
        okBtn = wx.Button(self, wx.ID_OK, "OK")
        okBtn.SetDefault()
        # Translators: the label of the cancel button in a dialog
        cancelBtn = wx.Button(self, wx.ID_CANCEL, "Cancel")
        for btn in (okBtn, cancelBtn):
            btnsizer.AddButton(btn)
        btnsizer.Realize()
        return btnsizer


class SnakDialog(SimpleDialog):
    """A Toast style notification  dialog for showing a simple message without a title."""

    def __init__(self, message, *args, dismiss_callback=None, **kwargs):
        self.message = message
        self.dismiss_callback = dismiss_callback
        super().__init__(*args, title="", style=0, **kwargs)
        self.CenterOnParent()

    def addControls(self, parent):
        ai = wx.ActivityIndicator(parent)
        ai.SetSizerProp("halign", "center")
        self.staticMessage = wx.StaticText(parent, -1, self.message)
        self.staticMessage.SetCanFocus(True)
        self.staticMessage.SetFocusFromKbd()
        self.Bind(wx.EVT_CLOSE, self.onClose, self)
        self.staticMessage.Bind(wx.EVT_KEY_UP, self.onKeyUp, self.staticMessage)
        ai.Start()

    @contextlib.contextmanager
    def ShowBriefly(self):
        try:
            wx.CallAfter(self.ShowModal)
            yield
        finally:
            wx.CallAfter(self.Close)
            wx.CallAfter(self.Destroy)

    def onClose(self, event):
        if event.CanVeto():
            if self.dismiss_callback is not None:
                should_close = self.dismiss_callback()
                if should_close:
                    self.Hide()
                    return
            event.Veto()
        else:
            self.Destroy()

    def onKeyUp(self, event):
        event.Skip()
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()

    def getButtons(self, parent):
        return


class AsyncSnakDialog:
    """A helper to make the use of SnakDialogs Ergonomic."""

    def __init__(
        self,
        task: LongRunningTask,
        done_callback: DoneCallback,
        *sdg_args,
        **sdg_kwargs,
    ):
        self.snak_dg = SnakDialog(*sdg_args, **sdg_kwargs)
        self.done_callback = done_callback
        self.future = THREADED_WORKER.submit(task).add_done_callback(
            self.on_future_completed
        )
        self.snak_dg.ShowModal()

    def on_future_completed(self, completed_future):
        self.Dismiss()
        wx.CallAfter(self.done_callback, completed_future)

    def Dismiss(self):
        if self.snak_dg:
            wx.CallAfter(self.snak_dg.Hide)
            wx.CallAfter(self.snak_dg.Destroy)
            wx.CallAfter(self.snak_dg.Parent.Enable)


@dataclass(frozen=True)
class ColumnDefn:
    title: str
    alignment: str
    width: int
    string_converter: t.Union[t.Callable[[t.Any], str], str]

    _ALIGNMENT_FLAGS = {
        "left": wx.LIST_FORMAT_LEFT,
        "center": wx.LIST_FORMAT_CENTRE,
        "right": wx.LIST_FORMAT_RIGHT,
    }

    @property
    def alignment_flag(self):
        flag = self._ALIGNMENT_FLAGS.get(self.alignment)
        if flag is not None:
            return flag
        raise ValueError(f"Unknown alignment directive {self.alignment}")


class ImmutableObjectListView(DialogListCtrl):
    """An immutable  list view that deals with objects rather than strings."""

    def __init__(
        self,
        *args,
        columns: t.Iterable[ColumnDefn] = (),
        objects: ObjectCollection = (),
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._objects = None
        self._columns = None
        self.Bind(wx.EVT_LIST_DELETE_ITEM, self.onDeleteItem, self)
        self.Bind(wx.EVT_LIST_DELETE_ALL_ITEMS, self.onDeleteAllItems, self)
        self.Bind(wx.EVT_LIST_INSERT_ITEM, self.onInsertItem, self)
        self.__is_modifying = False
        self.set_columns(columns)
        self.set_objects(objects)

    @contextlib.contextmanager
    def __unsafe_modify(self):
        self.__is_modifying = True
        yield
        self.__is_modifying = False

    def set_columns(self, columns):
        self.ClearAll()
        self._columns = columns
        for col in self._columns:
            self.AppendColumn(col.title, format=col.alignment_flag, width=col.width)
        for i in range(len(columns)):
            self.SetColumnWidth(i, 100)

    def set_objects(
        self, objects: ObjectCollection, focus_item: int = 0, set_focus=True
    ):
        """Clear the list view and insert the objects."""
        self._objects = objects
        self.set_columns(self._columns)
        string_converters = [c.string_converter for c in self._columns]
        with self.__unsafe_modify():
            for obj in self._objects:
                col_labels = []
                for to_str in string_converters:
                    col_labels.append(
                        getattr(obj, to_str) if not callable(to_str) else to_str(obj)
                    )
                self.Append(col_labels)
        if set_focus:
            self.set_focused_item(focus_item)

    def get_selected(self) -> t.Optional[t.Any]:
        """Return the currently selected object or None."""
        idx = self.GetFocusedItem()
        if idx != wx.NOT_FOUND:
            return self._objects[idx]

    def prevent_mutations(self):
        if not self.__is_modifying:
            raise RuntimeError(
                "List is immutable. Use 'ImmutableObjectListView.set_objects' instead"
            )

    def onDeleteItem(self, event):
        self.prevent_mutations()

    def onDeleteAllItems(self, event):
        ...

    def onInsertItem(self, event):
        self.prevent_mutations()


class EnumItemContainerMixin:
    """
    An item container that accepts an Enum as its choices argument.
    The Enum must provide a display property.
    """

    items_arg = None

    def __init__(self, *args, choice_enum, **kwargs):
        kwargs[self.items_arg] = [m.display for m in choice_enum]
        super().__init__(*args, **kwargs)
        self.choice_enum = choice_enum
        self.choice_members = tuple(choice_enum)
        if self.choice_members:
            self.SetSelection(0)

    def GetSelectedValue(self):
        return self.choice_members[self.GetSelection()]

    @property
    def SelectedValue(self):
        return self.GetSelectedValue()

    def SetSelectionByValue(self, value):
        if not isinstance(value, self.choice_enum):
            raise TypeError(f"{value} is not a {self.choice_enum}")
        self.SetSelection(self.choice_members.index(value))


class EnumRadioBox(EnumItemContainerMixin, wx.RadioBox):
    """A RadioBox that accepts enum as choices."""

    items_arg = "choices"


class EnumChoice(EnumItemContainerMixin, wx.Choice):
    items_arg = "choices"
