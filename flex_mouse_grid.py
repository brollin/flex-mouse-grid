# Written by timo, based on mousegrid written by timo and cleaned up a lot by aegis, heavily heavily
# edited by Tara. Finally, again heavily modified by brollin. Stole a lot of ideas from screen-spots
# by Andrew.
from .flex_store import FlexStore
from .ui_widgets import layout_text
from .ui_widgets import render_text
from talon import (
    app,
    canvas,
    Context,
    ctrl,
    Module,
    registry,
    ui,
    screen,
)
from talon.skia import Paint, Rect, Image
from talon.types.point import Point2d

import typing
import string
import time

import numpy as np

import subprocess
import sys
import os
import json
import base64


def hx(v: int) -> str:
    return "{:02x}".format(v)


mod = Module()

mod.tag(
    "flex_mouse_grid_showing",
    desc="Tag indicates whether the flex mouse grid is showing",
)

mod.tag(
    "flex_grid_boxes_showing",
    desc="Tag indicates whether the flex grid boxes are showing",
)

setting_letters_background_color = mod.setting(
    "flex_mouse_grid_letters_background_color",
    type=str,
    default="000000",
    desc="set the background color of the small letters in the flex mouse grid",
)

setting_row_highlighter = mod.setting(
    "flex_mouse_grid_row_highlighter",
    type=str,
    default="ff0000",
    desc="set the color of the row to highlight",
)

setting_large_number_color = mod.setting(
    "flex_mouse_grid_large_number_color",
    type=str,
    default="00ffff",
    desc="sets the color of the large number label in the superblock",
)

setting_small_letters_color = mod.setting(
    "flex_mouse_grid_small_letters_color",
    type=str,
    default="ffff55",
    desc="sets the color of the small letters label in the superblock",
)

setting_superblock_background_color = mod.setting(
    "flex_mouse_grid_superblock_background_color",
    type=str,
    default="ff55ff",
    desc="sets the background color of the superblock",
)

setting_superblock_stroke_color = mod.setting(
    "flex_mouse_grid_superblock_stroke_color",
    type=str,
    default="ffffff",
    desc="sets the background color of the superblock",
)

setting_field_size = mod.setting(
    "flex_mouse_grid_field_size",
    type=str,
    default="32",
    desc="sets the default size of the small grid blocks",
)

setting_superblock_transparency = mod.setting(
    "flex_mouse_grid_superblock_transparency",
    type=str,
    default="0x22",
    desc="sets the transparency of the superblocks",
)

setting_label_transparency = mod.setting(
    "flex_mouse_grid_label_transparency",
    type=str,
    default="0x99",
    desc="sets the transparency of the labels",
)

setting_startup_mode = mod.setting(
    "flex_mouse_grid_startup_mode",
    type=str,
    default="phonetic",
    desc="determines which mode the grid will be in each time the grid is reopened.",
)

setting_flex_grid_font = mod.setting(
    "flex_mouse_grid_font",
    type=str,
    default="arial rounded mt",
    desc="determines the default font",
)


ctx = Context()


class FlexMouseGrid:
    def __init__(self):
        self.screen = None
        self.rect: Rect = None
        self.history = []
        self.mcanvas = None
        self.columns = 0
        self.rows = 0
        self.superblocks = []
        self.selected_superblock = 0
        self.input_so_far = ""
        self.letters = string.ascii_lowercase
        self.morph = []

        # configured via settings
        self.field_size = int(setting_field_size.get())
        self.label_transparency = int(setting_label_transparency.get(), 16)
        self.bg_transparency = int(setting_superblock_transparency.get(), 16)
        self.pattern = setting_startup_mode.get()

        # visibility flags
        self.grid_showing = False
        self.rulers_showing = False
        self.points_showing = False
        self.boxes_showing = False
        self.boxes_threshold_view_showing = False
        self.info_showing = False

    def setup(self, *, rect: Rect = None, screen_index: int = -1):
        # get informaition on number and size of screens
        screens = ui.screens()

        # each if block here might set the rect to None to indicate failure
        # rect contains position, height, and width of the canvas
        if rect is not None:
            try:
                screen = ui.screen_containing(*rect.center)
            except Exception:
                rect = None

        if rect is None and screen_index >= 0:
            screen = screens[screen_index % len(screens)]
            rect = screen.rect

        # default the rect to the first screen
        if rect is None:
            screen = screens[0]
            rect = screen.rect

        self.rect = rect.copy()
        self.screen = screen

        # use the field size to calculate how many rows and how many columns there are
        self.columns = int(self.rect.width // self.field_size)
        self.rows = int(self.rect.height // self.field_size)

        self.history = []
        self.superblocks = []
        self.selected_superblock = 0
        self.input_so_far = ""

        # visibility flags
        self.grid_showing = False
        self.rulers_showing = False
        self.points_showing = False
        self.boxes_showing = False

        # points
        self.points_map_store = FlexStore("points", lambda: {})
        self.points_map = self.points_map_store.load()
        self.points_last_visited_index_map = {}

        # boxes
        self.box_config_store = FlexStore(
            "box_config",
            lambda: {
                "threshold": 25,
                "box_size_lower": 31,
                "box_size_upper": 400,
            },
        )
        self.box_config = self.box_config_store.load()
        self.boxes = []

        # flex grid
        self.grid_config_store = FlexStore(
            "grid_config",
            lambda: {
                "field_size": int(setting_field_size.get()),
                "label_transparency": int(setting_label_transparency.get(), 16),
                "bg_transparency": int(setting_superblock_transparency.get(), 16),
                "pattern": setting_startup_mode.get(),
            },
        )
        self.load_grid_config_from_store()

        if self.mcanvas is not None:
            self.mcanvas.close()
        self.mcanvas = canvas.Canvas.from_screen(screen)
        self.mcanvas.register("draw", self.draw)
        self.mcanvas.freeze()

    def add_partial_input(self, letter: str):
        # this logic changes which superblock is selected
        if letter.isdigit():
            self.selected_superblock = int(letter) - 1
            self.redraw()
            return

        # this logic collects letters. you can only collect up to two letters
        self.input_so_far += letter
        if len(self.input_so_far) >= 2:
            self.jump(self.input_so_far, self.selected_superblock)
            self.input_so_far = ""

        self.redraw()

    def adjust_bg_transparency(self, amount: int):
        self.bg_transparency += amount
        if self.bg_transparency < 0:
            self.bg_transparency = 0
        if self.bg_transparency > 255:
            self.bg_transparency = 255
        self.redraw()

    def adjust_label_transparency(self, amount: int):
        self.label_transparency += amount
        if self.label_transparency < 0:
            self.label_transparency = 0
        if self.label_transparency > 255:
            self.label_transparency = 255

        self.save_grid_config_to_store()
        self.redraw()

    def adjust_field_size(self, amount: int):
        self.field_size += amount
        if self.field_size < 5:
            self.field_size = 5

        self.columns = int(self.rect.width // self.field_size)
        self.rows = int(self.rect.height // self.field_size)
        self.superblocks = []

        self.save_grid_config_to_store()
        self.show_grid()
        self.redraw()

    def show_grid(self):
        self.grid_showing = True
        self.redraw()

    def hide_grid(self):
        if not self.grid_showing:
            return

        self.grid_showing = False
        self.redraw()

    def deactivate(self):
        self.points_showing = False
        self.boxes_showing = False
        self.boxes_threshold_view_showing = False
        self.grid_showing = False
        self.info_showing = False
        self.redraw()

        self.input_so_far = ""

    def redraw(self):
        if self.mcanvas:
            self.mcanvas.freeze()

    def draw(self, canvas):
        # for other-screen or individual-window grids
        canvas.translate(self.rect.x, self.rect.y)
        canvas.clip_rect(
            Rect(
                -self.field_size * 2,
                -self.field_size * 2,
                self.rect.width + self.field_size * 4,
                self.rect.height + self.field_size * 4,
            )
        )

        def draw_superblock():
            superblock_size = len(self.letters) * self.field_size

            colors = ["000055", "665566", "554444", "888855", "aa55aa", "55cccc"] * 100
            num = 1

            self.superblocks = []

            skipped_superblock = self.selected_superblock + 1

            if (
                int(self.rect.height) // superblock_size == 0
                and int(self.rect.width) // superblock_size == 0
            ):
                skipped_superblock = 1

            for row in range(0, int(self.rect.height) // superblock_size + 1):
                for col in range(0, int(self.rect.width) // superblock_size + 1):
                    canvas.paint.color = colors[(row + col) % len(colors)] + hx(
                        self.bg_transparency
                    )

                    # canvas.paint.color = "ffffff"
                    canvas.paint.style = Paint.Style.FILL
                    blockrect = Rect(
                        col * superblock_size,
                        row * superblock_size,
                        superblock_size,
                        superblock_size,
                    )
                    blockrect.right = min(blockrect.right, self.rect.width)
                    blockrect.bot = min(blockrect.bot, self.rect.height)
                    canvas.draw_rect(blockrect)

                    if skipped_superblock != num:
                        # attempt to change backround color on the superblock chosen

                        # canvas.paint.color = colors[(row + col) % len(colors)] + hx(self.bg_transparency)

                        canvas.paint.color = (
                            setting_superblock_background_color.get()
                            + hx(self.bg_transparency)
                        )
                        canvas.paint.style = Paint.Style.FILL
                        blockrect = Rect(
                            col * superblock_size,
                            row * superblock_size,
                            superblock_size,
                            superblock_size,
                        )
                        blockrect.right = min(blockrect.right, self.rect.width)
                        blockrect.bot = min(blockrect.bot, self.rect.height)
                        canvas.draw_rect(blockrect)

                        canvas.paint.color = setting_superblock_stroke_color.get() + hx(
                            self.bg_transparency
                        )
                        canvas.paint.style = Paint.Style.STROKE
                        canvas.paint.stroke_width = 5
                        blockrect = Rect(
                            col * superblock_size,
                            row * superblock_size,
                            superblock_size,
                            superblock_size,
                        )
                        blockrect.right = min(blockrect.right, self.rect.width)
                        blockrect.bot = min(blockrect.bot, self.rect.height)
                        canvas.draw_rect(blockrect)

                        # drawing the big number in the background

                        canvas.paint.style = Paint.Style.FILL
                        canvas.paint.textsize = int(superblock_size)
                        text_rect = canvas.paint.measure_text(str(num))[1]
                        # text_rect.center = blockrect.center
                        text_rect.x = blockrect.x
                        text_rect.y = blockrect.y
                        canvas.paint.color = setting_large_number_color.get() + hx(
                            self.bg_transparency
                        )
                        canvas.draw_text(
                            str(num), text_rect.x, text_rect.y + text_rect.height
                        )

                    self.superblocks.append(blockrect.copy())

                    num += 1

        def draw_text():
            canvas.paint.text_align = canvas.paint.TextAlign.CENTER
            canvas.paint.textsize = 17
            canvas.paint.typeface = setting_flex_grid_font.get()

            skip_it = False

            for row in range(0, self.rows + 1):
                for col in range(0, self.columns + 1):
                    if self.pattern == "checkers":
                        if (row % 2 == 0 and col % 2 == 0) or (
                            row % 2 == 1 and col % 2 == 1
                        ):
                            skip_it = True
                        else:
                            skip_it = False

                    if self.pattern == "frame" or self.pattern == "phonetic":
                        if (row % 26 == 0) or (col % 26 == 0):
                            skip_it = False
                        else:
                            skip_it = True

                    # draw the highlighter

                    base_rect = self.superblocks[self.selected_superblock].copy()

                    if (
                        row >= (base_rect.y / self.field_size)
                        and row <= (base_rect.y / self.field_size + len(self.letters))
                        and col >= (base_rect.x / self.field_size)
                        and col <= (base_rect.x / self.field_size + len(self.letters))
                    ):
                        within_selected_superblock = True

                        if (
                            within_selected_superblock
                            and len(self.input_so_far) == 1
                            and self.input_so_far.startswith(
                                self.letters[row % len(self.letters)]
                            )
                        ):
                            skip_it = False

                    if not (skip_it):
                        draw_letters(row, col)

        def draw_letters(row, col):
            # get letters
            # gets a letter from the alphabet of the form 'ab' or 'DA'
            text_string = f"{self.letters[row % len(self.letters)]}{self.letters[col % len(self.letters)]}"
            # this the measure text is the box around the text.
            canvas.paint.textsize = int(self.field_size * 3 / 5)
            # canvas.paint.textsize = int(field_size*4/5)
            text_rect = canvas.paint.measure_text(text_string)[1]

            background_rect = text_rect.copy()
            background_rect.center = Point2d(
                col * self.field_size + self.field_size / 2,
                row * self.field_size + self.field_size / 2,
            )
            background_rect = background_rect.inset(-4)

            # remove distracting letters from frame mode frames.
            if self.pattern == "frame":
                if self.letters[row % len(self.letters)] == "a":
                    # gets a letter from the alphabet of the form 'ab' or 'DA'
                    text_string = f"{self.letters[col % len(self.letters)]}"
                    # this the measure text is the box around the text.
                    canvas.paint.textsize = int(self.field_size * 3 / 5)
                    # canvas.paint.textsize = int(field_size*4/5)
                    text_rect = canvas.paint.measure_text(text_string)[
                        1
                    ]  # find out how many characters long the text is?
                    background_rect = text_rect.copy()
                    background_rect.center = Point2d(
                        col * self.field_size + self.field_size / 2,
                        row * self.field_size + self.field_size / 2,
                    )  # I think this re-centers the point?
                    background_rect = background_rect.inset(-4)
                elif self.letters[col % len(self.letters)] == "a":
                    text_string = f"{self.letters[row % len(self.letters)]}"

                    canvas.paint.textsize = int(self.field_size * 3 / 5)
                    # canvas.paint.textsize = int(field_size*4/5)
                    text_rect = canvas.paint.measure_text(text_string)[
                        1
                    ]  # find out how many characters long the text is?

                    background_rect = text_rect.copy()
                    background_rect.center = Point2d(
                        col * self.field_size + self.field_size / 2,
                        row * self.field_size + self.field_size / 2,
                    )  # I think this re-centers the point?
                    background_rect = background_rect.inset(-4)

            elif self.pattern == "phonetic":
                if self.letters[row % len(self.letters)] == "a":
                    # gets a letter from the alphabet of the form 'ab' or 'DA'
                    text_string = f"{self.letters[col % len(self.letters)]}"
                    # this the measure text is the box around the text.
                    canvas.paint.textsize = int(self.field_size * 3 / 5)
                    # canvas.paint.textsize = int(field_size*4/5)
                    text_rect = canvas.paint.measure_text(text_string)[
                        1
                    ]  # find out how many characters long the text is?
                    background_rect = text_rect.copy()
                    background_rect.center = Point2d(
                        col * self.field_size + self.field_size / 2,
                        row * self.field_size + self.field_size / 2,
                    )  # I think this re-centers the point?
                    background_rect = background_rect.inset(-4)
                elif self.letters[col % len(self.letters)] == "a":
                    # gets the phonetic words currently being used
                    text_string = f"{list(registry.lists['user.letter'][0].keys())[row%len(self.letters)]}"

                    canvas.paint.textsize = int(self.field_size * 3 / 5)
                    # canvas.paint.textsize = int(field_size*4/5)
                    text_rect = canvas.paint.measure_text(text_string)[
                        1
                    ]  # find out how many characters long the text is?

                    background_rect = text_rect.copy()
                    background_rect.center = Point2d(
                        col * self.field_size + self.field_size / 2,
                        row * self.field_size + self.field_size / 2,
                    )  # I think this re-centers the point?
                    background_rect = background_rect.inset(-4)

            if not (
                self.input_so_far.startswith(self.letters[row % len(self.letters)])
                or len(self.input_so_far) > 1
                and self.input_so_far.endswith(self.letters[col % len(self.letters)])
            ):
                canvas.paint.color = setting_letters_background_color.get() + hx(
                    self.label_transparency
                )
                canvas.paint.style = Paint.Style.FILL
                canvas.draw_rect(background_rect)
                canvas.paint.color = setting_small_letters_color.get() + hx(
                    self.label_transparency
                )
                # paint.style = Paint.Style.STROKE
                canvas.draw_text(
                    text_string,
                    col * self.field_size + self.field_size / 2,
                    row * self.field_size + self.field_size / 2 + text_rect.height / 2,
                )

            # sees if the background should be highlighted
            elif (
                self.input_so_far.startswith(self.letters[row % len(self.letters)])
                or len(self.input_so_far) > 1
                and self.input_so_far.endswith(self.letters[col % len(self.letters)])
            ):
                # draw columns of phonetic words
                phonetic_word = list(registry.lists["user.letter"][0].keys())[
                    col % len(self.letters)
                ]
                letter_list = list(phonetic_word)
                for index, letter in enumerate(letter_list):
                    if index == 0:
                        canvas.paint.color = setting_row_highlighter.get() + hx(
                            self.label_transparency
                        )
                        # check if someone has said a letter and highlight a row, or check if two
                        # letters have been said and highlight a column

                        # colors it the ordinary background.
                        text_string = f"{letter}"  # gets a letter from the alphabet of the form 'ab' or 'DA'
                        # this the measure text is the box around the text.
                        canvas.paint.textsize = int(self.field_size * 3 / 5)
                        # canvas.paint.textsize = int(field_size*4/5)
                        text_rect = canvas.paint.measure_text(text_string)[
                            1
                        ]  # find out how many characters long the text is?

                        background_rect = text_rect.copy()
                        background_rect.center = Point2d(
                            col * self.field_size + self.field_size / 2,
                            row * self.field_size
                            + (self.field_size / 2 + text_rect.height / 2)
                            * (index + 1),
                        )  # I think this re-centers the point?
                        background_rect = background_rect.inset(-4)
                        canvas.draw_rect(background_rect)
                        canvas.paint.color = setting_small_letters_color.get() + hx(
                            self.label_transparency
                        )
                        # paint.style = Paint.Style.STROKE
                        canvas.draw_text(
                            text_string,
                            col * self.field_size + (self.field_size / 2),
                            row * self.field_size
                            + (self.field_size / 2 + text_rect.height / 2)
                            * (index + 1),
                        )

                    elif self.pattern == "phonetic":
                        canvas.paint.color = (
                            setting_letters_background_color.get()
                            + hx(self.label_transparency)
                        )
                        # gets a letter from the alphabet of the form 'ab' or 'DA'
                        text_string = f"{letter}"
                        # this the measure text is the box around the text.
                        canvas.paint.textsize = int(self.field_size * 3 / 5)
                        # canvas.paint.textsize = int(field_size*4/5)
                        text_rect = canvas.paint.measure_text(text_string)[
                            1
                        ]  # find out how many characters long the text is?

                        background_rect = text_rect.copy()
                        background_rect.center = Point2d(
                            col * self.field_size + self.field_size / 2,
                            row * self.field_size
                            + (self.field_size / 2 + text_rect.height / 2)
                            * (index + 1),
                        )  # I think this re-centers the point?
                        background_rect = background_rect.inset(-4)
                        canvas.draw_rect(background_rect)
                        canvas.paint.color = setting_small_letters_color.get() + hx(
                            self.label_transparency
                        )
                        # paint.style = Paint.Style.STROKE
                        canvas.draw_text(
                            text_string,
                            col * self.field_size + (self.field_size / 2),
                            row * self.field_size
                            + (self.field_size / 2 + text_rect.height / 2)
                            * (index + 1),
                        )

        def draw_rulers():
            for x_pos, align in [
                (-3, canvas.paint.TextAlign.RIGHT),
                (self.rect.width + 3, canvas.paint.TextAlign.LEFT),
            ]:
                canvas.paint.text_align = align
                canvas.paint.textsize = 17
                canvas.paint.color = "ffffffff"

                for row in range(0, self.rows + 1):
                    text_string = self.letters[row % len(self.letters)] + "_"
                    text_rect = canvas.paint.measure_text(text_string)[1]
                    background_rect = text_rect.copy()
                    background_rect.x = x_pos
                    background_rect.y = (
                        row * self.field_size
                        + self.field_size / 2
                        + text_rect.height / 2
                    )
                    canvas.draw_text(text_string, background_rect.x, background_rect.y)

            for y_pos in [-3, self.rect.height + 3 + 17]:
                canvas.paint.text_align = canvas.paint.TextAlign.CENTER
                canvas.paint.textsize = 17
                canvas.paint.color = "ffffffff"
                for col in range(0, self.columns + 1):
                    text_string = "_" + self.letters[col % len(self.letters)]
                    text_rect = canvas.paint.measure_text(text_string)[1]
                    background_rect = text_rect.copy()
                    background_rect.x = col * self.field_size + self.field_size / 2
                    background_rect.y = y_pos
                    canvas.draw_text(text_string, background_rect.x, background_rect.y)

        def draw_point_labels():
            canvas.paint.text_align = canvas.paint.TextAlign.LEFT
            canvas.paint.textsize = int(self.field_size * 3 / 5)

            for label, points in self.points_map.items():
                for index, point in enumerate(points):
                    # draw point label text
                    if len(points) > 1:
                        point_label = label + f" ({str(index + 1)})"
                    else:
                        point_label = label
                    text_rect = canvas.paint.measure_text(point_label)[1]
                    text_rect = text_rect.inset(-2)
                    canvas.paint.color = setting_small_letters_color.get()
                    canvas.draw_text(
                        point_label, point.x + 3, point.y + text_rect.height * 3 // 4
                    )

                    # draw transparent label box
                    background_rect = text_rect.copy()
                    background_rect.x = point.x
                    background_rect.y = point.y
                    canvas.paint.color = setting_letters_background_color.get() + hx(
                        self.label_transparency
                    )
                    canvas.paint.style = Paint.Style.FILL
                    canvas.draw_rect(background_rect)

                    # draw a dot for the exact location of the point
                    canvas.paint.color = "ffffff"
                    canvas.draw_circle(point.x, point.y, 2)

        def draw_boxes():
            canvas.paint.text_align = canvas.paint.TextAlign.LEFT
            canvas.paint.textsize = int(self.field_size * 3 / 5)

            for index, box in enumerate(self.boxes):
                point_label = str(index)
                text_rect = canvas.paint.measure_text(point_label)[1]
                background_rect = text_rect.copy()
                background_rect.x = box.x + 1
                background_rect.y = box.y + box.height - 2 - text_rect.height
                canvas.paint.color = "000000ff"
                canvas.paint.style = Paint.Style.FILL
                canvas.draw_rect(background_rect)

                canvas.paint.color = setting_small_letters_color.get()
                canvas.draw_text(
                    point_label,
                    box.x + 1,
                    box.y + box.height - 2,
                )

                # draw border of label box
                canvas.paint.color = "ff00ff"
                canvas.paint.style = Paint.Style.STROKE
                canvas.draw_rect(box)

                canvas.paint.style = Paint.Style.FILL

        def draw_threshold():
            if len(self.morph):
                image = Image.from_array(self.morph)
                src = Rect(0, 0, image.width, image.height)
                canvas.draw_image_rect(image, src, src)

        def draw_info():
            # retrieve the app-specific box detection configuration
            threshold = self.box_config["threshold"]
            box_size_lower = self.box_config["box_size_lower"]
            box_size_upper = self.box_config["box_size_upper"]

            info_text = (
                "GRID CONFIG====================\n"
                + f"  pattern:                {self.pattern}\n"
                + f"  field size:             {self.field_size}\n"
                + f"  label transparency:     {self.label_transparency}\n"
                + f"  bg transparency:        {self.bg_transparency}\n"
                + "\n"
                + "BOX CONFIG=====================\n"
                + f"  box size lower bound:   {box_size_lower}\n"
                + f"  box size upper bound:   {box_size_upper}\n"
                + f"  threshold:              {threshold}\n"
            )

            canvas.paint.text_align = canvas.paint.TextAlign.LEFT
            canvas.paint.textsize = int(self.field_size * 3 / 5)
            canvas.paint.typeface = "courier"
            canvas.paint.color = "55a3fb"
            (w, h), formatted_text = layout_text(info_text, canvas.paint, 800)
            x, y = self.rect.width // 2, self.rect.height // 2

            canvas.paint.color = "000000"
            canvas.paint.style = Paint.Style.FILL
            background_rect = Rect(x, y + 5, w, h)
            background_rect = background_rect.inset(-4)
            canvas.draw_rect(background_rect)
            canvas.paint.color = "ffffff"
            canvas.paint.style = Paint.Style.STROKE
            canvas.paint.stroke_width = 1
            canvas.draw_rect(background_rect)
            render_text(canvas, formatted_text, x, y)

            spacing = 15
            lower_rect = Rect(
                background_rect.x,
                background_rect.y + h + spacing,
                box_size_lower,
                box_size_lower,
            )
            upper_rect = Rect(
                background_rect.x + lower_rect.width + spacing,
                background_rect.y + h + spacing,
                box_size_upper,
                box_size_upper,
            )

            canvas.draw_rect(lower_rect)
            canvas.draw_rect(upper_rect)
            canvas.paint.color = "000000ff"
            canvas.paint.style = Paint.Style.FILL
            canvas.draw_rect(lower_rect)
            canvas.draw_rect(upper_rect)
            canvas.paint.text_align = canvas.paint.TextAlign.CENTER
            text_rect = canvas.paint.measure_text("1")[1]
            canvas.paint.color = "55a3fb"
            canvas.draw_text(
                str(box_size_lower),
                lower_rect.center.x,
                lower_rect.center.y + text_rect.height // 4,
            )
            canvas.draw_text(
                str(box_size_upper),
                upper_rect.center.x,
                upper_rect.center.y + text_rect.height // 4,
            )

        if self.grid_showing:
            draw_superblock()
            draw_text()

            if self.rulers_showing:
                draw_rulers()

        if self.points_showing:
            draw_point_labels()

        if self.boxes_threshold_view_showing:
            draw_threshold()

        if self.boxes_showing:
            draw_boxes()

        if self.info_showing:
            draw_info()

    def load_grid_config_from_store(self):
        self.grid_config = self.grid_config_store.load()
        self.field_size = self.grid_config["field_size"]
        self.label_transparency = self.grid_config["label_transparency"]
        self.bg_transparency = self.grid_config["bg_transparency"]
        self.pattern = self.grid_config["pattern"]

    def save_grid_config_to_store(self):
        self.grid_config_store.save(
            {
                "field_size": self.field_size,
                "label_transparency": self.label_transparency,
                "bg_transparency": self.bg_transparency,
                "pattern": self.pattern,
            }
        )

    def save_points(self):
        self.points_map_store.save(self.points_map)

    def save_box_config(self):
        self.box_config_store.save(self.box_config)

    def reset_window_context(self):
        # reload the stores for the current active window
        self.points_map = self.points_map_store.load()
        self.box_config = self.box_config_store.load()
        self.load_grid_config_from_store()

        # reset our rectangle to capture the active window
        self.rect = ui.active_window().rect.copy()

    def map_new_point_here(self, point_name):
        self.reset_window_context()

        x, y = ctrl.mouse_pos()

        # points are always relative to canvas
        self.points_map[point_name] = [Point2d(x - self.rect.x, y - self.rect.y)]

        self.save_points()

        self.points_showing = True
        self.redraw()

    def map_new_points_by_letter(self, point_name, spoken_letters):
        self.reset_window_context()

        if len(spoken_letters) % 2 != 0:
            print("uneven number of letters supplied")
            return

        self.points_map[point_name] = []

        for point_index in range(0, len(spoken_letters), 2):
            self.points_map[point_name].append(
                self.get_label_position(
                    spoken_letters[point_index : point_index + 2],
                    number=self.selected_superblock,
                    relative=True,
                )
            )

        self.save_points()

        self.points_showing = True
        self.redraw()

    def map_new_points_by_box(self, point_name, box_number_list):
        self.reset_window_context()

        points = []
        for box_number in box_number_list:
            if box_number >= len(self.boxes):
                print("box does not exist:", box_number)
                continue

            box_center = self.boxes[box_number].center
            points.append(Point2d(box_center.x, box_center.y))

        self.points_map[point_name] = points
        self.save_points()

        self.points_showing = True
        self.boxes_showing = False
        self.redraw()

    def map_new_points_by_box_range(self, point_name, box_number_range):
        self.reset_window_context()

        if len(box_number_range) != 2:
            print("cannot find box range with input:", box_number_range)
            return

        # allow doing ranges in reverse
        if box_number_range[0] < box_number_range[1]:
            box_number_list = list(range(box_number_range[0], box_number_range[1] + 1))
        else:
            box_number_list = list(
                range(box_number_range[0], box_number_range[1] - 1, -1)
            )

        self.map_new_points_by_box(point_name, box_number_list)

    def unmap_point(self, point_name):
        self.reset_window_context()

        if point_name == "":
            self.points_map = {}
            self.save_points()
            self.redraw()
            return

        if point_name not in self.points_map:
            print("point", point_name, "not found")
            return

        del self.points_map[point_name]
        self.save_points()
        self.redraw()

    def go_to_point(self, point_name, index, relative=False):
        self.reset_window_context()

        if point_name not in self.points_map:
            print("point", point_name, "not found")
            return

        # the index we get is 1-based, but we want 0-based
        element_index = index - 1

        if relative:
            if point_name in self.points_last_visited_index_map:
                point_list_length = len(self.points_map[point_name])
                element_index = (
                    self.points_last_visited_index_map[point_name] + index
                ) % point_list_length
            else:
                element_index = 0

        point = self.points_map[point_name][element_index]
        self.points_last_visited_index_map[point_name] = element_index

        # points are always relative to canvas
        ctrl.mouse_move(self.rect.x + point.x, self.rect.y + point.y)
        self.redraw()

    def mouse_click(self, mouse_button):
        if mouse_button >= 0:
            ctrl.mouse_click(button=mouse_button, down=True)
            time.sleep(0.05)
            ctrl.mouse_click(button=mouse_button, up=True)

    def temporarily_hide_everything(self):
        self.saved_visibility = (
            self.points_showing,
            self.boxes_showing,
            self.grid_showing,
        )
        if self.points_showing or self.boxes_showing or self.grid_showing:
            self.points_showing = False
            self.boxes_showing = False
            self.grid_showing = False
            self.redraw()
            time.sleep(0.05)

    def restore_everything(self):
        p, b, g = self.saved_visibility
        self.points_showing = p
        self.boxes_showing = b
        self.grid_showing = g

    def find_boxes(self, scan=False):
        self.reset_window_context()

        # temporarily hide everything that we have drawn so that it doesn't interfere with box detection
        self.temporarily_hide_everything()

        # use a threshold of -1 to indicate that we should scan for a good threshold
        threshold = -1 if scan else self.box_config["threshold"]

        # retrieve the app-specific box detection configuration
        box_size_lower = self.box_config["box_size_lower"]
        box_size_upper = self.box_config["box_size_upper"]

        # perform box detection
        self.find_boxes_with_config(threshold, box_size_lower, box_size_upper)

        # save final threshold
        self.save_box_config()

        # restore everything previously hidden and show boxes
        self.restore_everything()
        self.boxes_showing = True
        self.redraw()

    def find_boxes_with_config(self, threshold, box_size_lower, box_size_upper):
        current_directory = os.path.dirname(__file__)
        find_boxes_path = os.path.join(current_directory, ".find_boxes.py")

        image_array = np.array(screen.capture_rect(self.rect), dtype=np.uint8)
        image_no_alpha = image_array[:, :, :3]
        img = base64.b64encode(image_no_alpha.tobytes()).decode("utf-8")

        # run openCV script to find boxes in a separate process
        process = subprocess.run(
            (sys.executable, find_boxes_path),
            capture_output=True,
            input=json.dumps(
                {
                    "threshold": threshold,
                    "box_size_lower": box_size_lower,
                    "box_size_upper": box_size_upper,
                    "img": img,
                    "width": image_array.shape[1],
                    "height": image_array.shape[0],
                },
                separators=(",", ":"),
            ),
            text=True,
        )

        # print(process.stdout)
        # print(process.stderr)

        process_output = json.loads(process.stdout)
        boxes = process_output["boxes"]
        self.boxes = [Rect(box["x"], box["y"], box["w"], box["h"]) for box in boxes]
        self.box_config["threshold"] = process_output["threshold"]

    def go_to_box(self, box_number):
        if box_number >= len(self.boxes):
            print("box number does not exist")
            return

        box = self.boxes[box_number]
        ctrl.mouse_move(self.rect.x + box.center.x, self.rect.y + box.center.y)

        self.boxes_showing = False
        self.redraw()

    def get_label_position(self, spoken_letters, number=-1, relative=False):
        base_rect = self.superblocks[number].copy()

        if not relative:
            base_rect.x += self.rect.x
            base_rect.y += self.rect.y

        x_idx = self.letters.index(spoken_letters[1])
        y_idx = self.letters.index(spoken_letters[0])

        return Point2d(
            base_rect.x + x_idx * self.field_size + self.field_size / 2,
            base_rect.y + y_idx * self.field_size + self.field_size / 2,
        )

    def jump(self, spoken_letters, number=-1):
        point = self.get_label_position(spoken_letters, number=number)
        ctrl.mouse_move(point.x, point.y)

        self.input_so_far = ""
        self.redraw()

    def set_pattern(self, pattern: str):
        self.pattern = pattern
        self.save_grid_config_to_store()
        self.redraw()

    def toggle_rulers(self):
        self.rulers_showing = not self.rulers_showing
        self.redraw()

    def toggle_points(self, onoff=None):
        self.reset_window_context()

        if onoff is not None:
            self.points_showing = onoff
        else:
            self.points_showing = not self.points_showing
        self.redraw()

    def toggle_boxes(self, onoff=None):
        if onoff is not None:
            self.boxes_showing = onoff
        else:
            self.boxes_showing = not self.boxes_showing
        self.redraw()

    def toggle_boxes_threshold_view(self):
        self.boxes_threshold_view_showing = not self.boxes_threshold_view_showing
        self.redraw()

    def toggle_info(self):
        self.reset_window_context()

        self.info_showing = not self.info_showing
        self.redraw()


mg = FlexMouseGrid()
app.register("ready", mg.setup)


@mod.action_class
class GridActions:
    def flex_grid_activate():
        """Place mouse grid over first screen"""
        mg.deactivate()
        mg.setup(rect=ui.screens()[0].rect)
        mg.show_grid()

        ctx.tags = ["user.flex_mouse_grid_showing"]

    def flex_grid_place_window():
        """Place mouse grid over the currently active window"""
        # mg.deactivate()
        mg.setup(rect=ui.active_window().rect)
        mg.show_grid()

        ctx.tags = ["user.flex_mouse_grid_showing"]

    def flex_grid_select_screen(screen: int):
        """Place mouse grid over specified screen"""
        mg.deactivate()

        screen_index = screen - 1
        if mg.mcanvas == None:
            mg.setup(screen_index=screen_index)
        elif mg.rect != ui.screens()[screen_index].rect:
            mg.setup(rect=ui.screens()[screen_index].rect)

        mg.show_grid()

        ctx.tags = ["user.flex_mouse_grid_showing"]

    def flex_grid_deactivate():
        """Deactivate/close the grid"""
        mg.deactivate()

        ctx.tags = []

    def flex_grid_hide_grid():
        """Hide the grid"""
        mg.hide_grid()

    def flex_grid_show_grid():
        """Show the grid"""
        mg.show_grid()

    def flex_grid_rulers_toggle():
        """Show or hide rulers all around the window"""
        mg.toggle_rulers()

    def flex_grid_input_partial(letter: str):
        """Input one letter to highlight a row or column"""
        mg.add_partial_input(str(letter))

    def flex_grid_input_horizontal(letter: str):
        """This command is for if you chose the wrong row and you want to choose a different row before choosing a column"""
        mg.input_so_far = ""
        mg.add_partial_input(str(letter))

    # GRID CONFIG
    def flex_grid_checkers():
        """Set pattern to checkers"""
        mg.set_pattern("checkers")

    def flex_grid_frame():
        """Set pattern to frame"""
        mg.set_pattern("frame")

    def flex_grid_full():
        """Set pattern to full"""
        mg.set_pattern("full")

    def flex_grid_phonetic():
        """Set pattern to phonetic"""
        mg.set_pattern("phonetic")

    def flex_grid_adjust_bg_transparency(amount: int):
        """Increase or decrease the opacity of the background of the flex mouse grid (also returns new value)"""
        mg.adjust_bg_transparency(amount)

    def flex_grid_adjust_label_transparency(amount: int):
        """Increase or decrease the opacity of the labels behind text for the flex mouse grid (also returns new value)"""
        mg.adjust_label_transparency(amount)

    def flex_grid_adjust_size(amount: int):
        """Increase or decrease size of everything"""
        mg.adjust_field_size(amount)

    # POINTS
    def flex_grid_points_toggle(onoff: int):
        """Show or hide mapped points"""
        mg.toggle_points(onoff=onoff == 1)

    def flex_grid_map_point_here(point_name: str):
        """Map a new point where the mouse cursor currently is"""
        mg.map_new_point_here(point_name)

    def flex_grid_map_points_by_letter(point_name: str, letter_list: typing.List[str]):
        """Map a new point or points by letter coordinates"""
        mg.map_new_points_by_letter(point_name, letter_list)

    def flex_grid_map_points_by_box(point_name: str, box_number_list: typing.List[int]):
        """Map a new point or points by box number(s)"""
        mg.map_new_points_by_box(point_name, box_number_list)

    def flex_grid_map_points_by_box_range(
        point_name: str, box_number_list: typing.List[int]
    ):
        """Map a new point or points by box number range"""
        mg.map_new_points_by_box_range(point_name, box_number_list)

    def flex_grid_unmap_point(point_name: str):
        """Unmap a point or all points"""
        mg.unmap_point(point_name)

    def flex_grid_go_to_point(point_name: str, index: int, mouse_button: int):
        """Go to a point, optionally click it"""
        mg.go_to_point(point_name, index)
        mg.mouse_click(mouse_button)

    def flex_grid_go_to_point_relative(point_name: str, delta: int):
        """Go to a point relative to the last visited point in a list"""
        mg.go_to_point(point_name, delta, relative=True)

    # BOXES
    def flex_grid_boxes_toggle(onoff: int):
        """Show or hide boxes"""
        mg.toggle_boxes(onoff=onoff == 1)

        ctx.tags = []

    def flex_grid_boxes_threshold_view_toggle():
        """Show or hide boxes"""
        mg.toggle_boxes_threshold_view()

    def flex_grid_find_boxes():
        """Find all boxes, label with hints"""
        mg.find_boxes()

        ctx.tags = ["user.flex_grid_boxes_showing"]

    def flex_grid_setup_boxes():
        """Do a binary search to find best box configuration"""
        mg.find_boxes(scan=True)

        ctx.tags = ["user.flex_grid_boxes_showing"]

    def flex_grid_go_to_box(box_number: int, mouse_button: int):
        """Go to a box"""
        mg.go_to_box(box_number)
        mg.mouse_click(mouse_button)

    def flex_grid_box_config_change(parameter: str, delta: int):
        """Change box configuration parameter by delta"""
        mg.box_config[parameter] += delta
        mg.save_box_config()
        mg.find_boxes()

    def flex_grid_info_toggle():
        """Show or hide informational UI"""
        mg.toggle_info()
