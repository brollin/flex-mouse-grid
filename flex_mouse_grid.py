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
    settings,
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

mod.setting(
    "flex_mouse_grid_letters_background_color",
    type=str,
    default="000000",
    desc="set the background color of the small letters in the flex mouse grid",
)

mod.setting(
    "flex_mouse_grid_row_highlighter",
    type=str,
    default="ff0000",
    desc="set the color of the row to highlight",
)

mod.setting(
    "flex_mouse_grid_large_number_color",
    type=str,
    default="00ffff",
    desc="sets the color of the large number label in the superblock",
)

mod.setting(
    "flex_mouse_grid_small_letters_color",
    type=str,
    default="ffff55",
    desc="sets the color of the small letters label in the superblock",
)

mod.setting(
    "flex_mouse_grid_superblock_background_color",
    type=str,
    default="ff55ff",
    desc="sets the background color of the superblock",
)

mod.setting(
    "flex_mouse_grid_superblock_stroke_color",
    type=str,
    default="ffffff",
    desc="sets the background color of the superblock",
)

mod.setting(
    "flex_mouse_grid_field_size",
    type=str,
    default="32",
    desc="sets the default size of the small grid blocks",
)

mod.setting(
    "flex_mouse_grid_superblock_transparency",
    type=str,
    default="0x22",
    desc="sets the transparency of the superblocks",
)

mod.setting(
    "flex_mouse_grid_label_transparency",
    type=str,
    default="0x99",
    desc="sets the transparency of the labels",
)

mod.setting(
    "flex_mouse_grid_startup_mode",
    type=str,
    default="phonetic",
    desc="determines which mode the grid will be in each time the grid is reopened.",
)

mod.setting(
    "flex_mouse_grid_font",
    type=str,
    default="arial rounded mt",
    desc="determines the default font",
)


ctx = Context()


def interpolate_points(p1, p2, num_points):
    return [
        Point2d(
            p1.x + (p2.x - p1.x) * i / (num_points - 1),
            p1.y + (p2.y - p1.y) * i / (num_points - 1),
        )
        for i in range(num_points)
    ]


def transform_image_space_to_window_space(image_width, image_height, window_rect, rect):
    # transform the given rect from image (pixel) coordinates to window coordinates
    # in this case, the image is a screenshot of the window
    horizontal_scale = window_rect.width / image_width
    vertical_scale = window_rect.height / image_height
    return Rect(
        round(rect.x * horizontal_scale),
        rect.y * vertical_scale,
        round(rect.width * horizontal_scale),
        rect.height * vertical_scale,
    )


def get_fg_setting(name):
    return settings.get("user.flex_mouse_grid_" + name)


class FlexMouseGrid:
    def __init__(self):
        self.screen = None
        # Rectangle that defines the area for the total canvas - e.g. the current screen or window
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
        self.__reset_visibility_flags()

    def __reset_visibility_flags(self):
        self.grid_showing = False
        self.rulers_showing = False
        self.points_showing = False
        self.boxes_showing = False
        self.boxes_threshold_view_showing = False
        self.info_showing = False

    def __create_canvas(self, screen):
        if self.mcanvas is not None:
            self.mcanvas.close()
        self.mcanvas = canvas.Canvas.from_screen(screen)
        self.mcanvas.register("draw", self.draw)
        self.mcanvas.freeze()

    def setup(self, *, rect: Rect = None, screen_index: int = -1):
        # configured via settings
        self.field_size = int(get_fg_setting("field_size"))
        self.label_transparency = int(get_fg_setting("label_transparency"), 16)
        self.label_transparency_hx = hx(self.label_transparency)
        self.bg_transparency = int(get_fg_setting("superblock_transparency"), 16)
        self.pattern = get_fg_setting("startup_mode")

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

        self.history = []
        self.superblocks = []
        self.selected_superblock = 0
        self.input_so_far = ""

        self.__reset_visibility_flags()

        # points
        self.points_map_store = FlexStore("points", lambda: {})
        self.points_map = self.points_map_store.load()
        self.points_last_visited_index_map = {}

        # boxes
        self.box_config_store = FlexStore(
            "box_config",
            lambda: {
                "locked": False,
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
                "field_size": int(get_fg_setting("field_size")),
                "label_transparency": int(get_fg_setting("label_transparency"), 16),
                "bg_transparency": int(get_fg_setting("superblock_transparency"), 16),
                "pattern": get_fg_setting("startup_mode"),
            },
        )
        self.load_grid_config_from_store()

        # use the field size to calculate how many rows and how many columns there are
        self.columns = int(self.rect.width // self.field_size)
        self.rows = int(self.rect.height // self.field_size)

        self.__create_canvas(screen)

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
        self.__reset_visibility_flags()
        self.redraw()

        # Close the canvas when deactivating
        if self.mcanvas is not None:
            self.mcanvas.close()
            self.mcanvas = None

        self.input_so_far = ""

    def redraw(self):
        if self.mcanvas:
            self.mcanvas.freeze()
        else:
            self.__create_canvas(self.screen)

    def _make_blockrect(self, col, row, size):
        rect = Rect(col * size, row * size, size, size)
        rect.right = min(rect.right, self.rect.width)
        rect.bot = min(rect.bot, self.rect.height)
        return rect

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
        user_letter_words = list(registry.lists["user.letter"][0].keys())

        def draw_():
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

        def draw_superblock():
            superblock_size = len(self.letters) * self.field_size
            self.superblocks = []

            skipped_superblock = self.selected_superblock + 1

            blocks_fitting_in_canvas_y = int(self.rect.height) // superblock_size
            blocks_fitting_in_canvas_x = int(self.rect.width) // superblock_size
            if blocks_fitting_in_canvas_y == 0 and blocks_fitting_in_canvas_x == 0:
                skipped_superblock = 1

            block_num = 1
            # Draw backgrounds for all except the current block
            for row in range(0, blocks_fitting_in_canvas_y + 1):
                for col in range(0, blocks_fitting_in_canvas_x + 1):
                    blockrect = self._make_blockrect(col, row, superblock_size)
                    if skipped_superblock != block_num:
                        draw_superblock_bg(blockrect, block_num)
                    self.superblocks.append(blockrect.copy())
                    block_num += 1

        def draw_superblock_bg(blockrect, block_num):
            superblock_size = len(self.letters) * self.field_size
            # Background
            canvas.paint.color = get_fg_setting("superblock_background_color") + hx(
                self.bg_transparency
            )
            canvas.paint.style = Paint.Style.FILL
            canvas.draw_rect(blockrect)

            # Border
            canvas.paint.color = get_fg_setting("superblock_stroke_color") + hx(
                self.bg_transparency
            )
            canvas.paint.style = Paint.Style.STROKE
            canvas.paint.stroke_width = 5
            canvas.draw_rect(blockrect)

            # drawing the big number in the background
            canvas.paint.style = Paint.Style.FILL
            canvas.paint.textsize = int(superblock_size)
            text_rect = canvas.paint.measure_text(str(block_num))[1]
            # text_rect.center = blockrect.center
            text_rect.x = blockrect.x
            text_rect.y = blockrect.y
            canvas.paint.color = get_fg_setting("large_number_color") + hx(
                self.bg_transparency
            )
            canvas.draw_text(str(block_num), text_rect.x, text_rect.y + text_rect.height)

        def draw_text():
            canvas.paint.text_align = canvas.paint.TextAlign.CENTER
            canvas.paint.textsize = 17
            canvas.paint.typeface = get_fg_setting("font")
            for row in range(0, self.rows + 1):
                for col in range(0, self.columns + 1):
                    if should_draw_letter(col, row):
                        draw_letters(row, col)

        def should_draw_letter(col, row):
            curr_row_letter = self.letters[row % len(self.letters)]
            base_rect = self.superblocks[self.selected_superblock]
            within_selected_superblock = (
                row >= (base_rect.y / self.field_size)
                and row <= (base_rect.y / self.field_size + len(self.letters))
                and col >= (base_rect.x / self.field_size)
                and col <= (base_rect.x / self.field_size + len(self.letters))
            )
            if (
                within_selected_superblock
                and len(self.input_so_far) == 1
                and self.input_so_far.startswith(curr_row_letter)
            ):
                return True
            skip_it = False
            if self.pattern == "checkers":
                even_row_col = row % 2 == 0 and col % 2 == 0
                odd_row_col = row % 2 == 1 and col % 2 == 1
                if even_row_col or odd_row_col:
                    skip_it = True
                else:
                    skip_it = False

            if self.pattern == "frame" or self.pattern == "phonetic":
                LETTERS_IN_ALPHABET = 26
                # Only draw cols at row alphabet ends, and vise versa
                if (row % LETTERS_IN_ALPHABET == 0) or (col % LETTERS_IN_ALPHABET == 0):
                    skip_it = False
                else:
                    skip_it = True
            return not skip_it

        def draw_letters(row, col):
            curr_row_letter = self.letters[row % len(self.letters)]
            curr_col_letter = self.letters[col % len(self.letters)]
            # gets a letter from the alphabet of the form 'ab' or 'DA'
            text_string = f"{curr_row_letter}{curr_col_letter}"
            # remove distracting letters from frame mode frames.
            if self.pattern == "frame":
                if curr_row_letter == "a":
                    text_string = curr_col_letter
                elif curr_col_letter == "a":
                    text_string = curr_row_letter
            elif self.pattern == "phonetic":
                if curr_row_letter == "a":
                    text_string = curr_col_letter
                elif curr_col_letter == "a":
                    # gets the phonetic words currently being used
                    text_string = user_letter_words[row % len(self.letters)]
            background_rect, text_rect = _make_centered_bg_rect(text_string, col, row)

            # check if someone has said a letter and highlight a row
            this_row_selected = self.input_so_far.startswith(curr_row_letter)


            if not this_row_selected: # Draw regular grid letters
                # BG
                canvas.paint.color = get_fg_setting("letters_background_color") + self.label_transparency_hx
                canvas.paint.style = Paint.Style.FILL
                canvas.draw_rect(background_rect)

                # Text
                canvas.paint.color = get_fg_setting("small_letters_color") + self.label_transparency_hx
                canvas.draw_text(
                    text_string,
                    col_field_center(col),
                    row_field_text_center(row, text_rect, 0),
                )

            # sees if the background should be highlighted
            else:
                # Draw this letter highlighted as part of the current row highlight
                canvas.paint.color = (
                    get_fg_setting("row_highlighter") + self.label_transparency_hx
                )
                phonetic_word = user_letter_words[col % len(self.letters)]
                col_label_letter = phonetic_word[0] if self.pattern == "phonetic" else curr_col_letter
                _draw_colored_text_box(
                    text_rect,
                    col_label_letter,
                    col,
                    row,
                )
                if self.pattern == "phonetic":
                    draw_selected_row_phonetic_letter(text_rect, phonetic_word, col, row)

        def draw_selected_row_phonetic_letter(text_rect, phonetic_word, col, row):
            # Leave row highlight for first letter, but draw the rest of the phonetic letters normal
            canvas.paint.color = (
                get_fg_setting("row_highlighter") + self.label_transparency_hx
            )
            # draw columns of phonetic words
            for letter_index, letter_word in enumerate(list(phonetic_word)):
                _draw_colored_text_box(
                    text_rect,
                    letter_word,
                    col,
                    row,
                    letter_index,
                )
                canvas.paint.color = (
                    get_fg_setting("letters_background_color")
                    + self.label_transparency_hx
                )

        def col_field_center(col):
            return col * self.field_size + self.field_size / 2

        def row_field_text_center(row, text_rect, row_letter_index=0):
            # Row letter index is used to offset the row position of the
            # text if it is was a multi-char string, eg for phonetic column
            # labels.
            return row * self.field_size + (
                self.field_size / 2 + text_rect.height / 2
            ) * (row_letter_index + 1)

        def _make_centered_bg_rect(text_string, col, row, row_letter_index=0):
            # this the measure text is the box around the text.
            canvas.paint.textsize = int(self.field_size * 3 / 5)
            # canvas.paint.textsize = int(field_size*4/5)
            text_rect = canvas.paint.measure_text(text_string)[1]
            background_rect = text_rect.copy()
            background_rect.center = Point2d(
                col_field_center(col),
                row_field_text_center(row, text_rect, row_letter_index),
            )  # I think this re-centers the point?
            background_rect = background_rect.inset(-4)
            return background_rect, text_rect

        def _draw_colored_text_box(
            text_rect, letter, col, row, index=0
        ):
            text_string = f"{letter}"
            background_rect, _ = _make_centered_bg_rect(
                text_string, col, row, row_letter_index=index
            )
            canvas.draw_rect(background_rect)
            canvas.paint.color = get_fg_setting("small_letters_color") + self.label_transparency_hx
            # paint.style = Paint.Style.STROKE
            canvas.draw_text(
                text_string,
                col_field_center(col),
                row * self.field_size
                + (self.field_size / 2 + text_rect.height / 2) * (index + 1),
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
                    background_rect.x = col_field_center(col)
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
                    canvas.paint.color = get_fg_setting("small_letters_color")
                    canvas.draw_text(
                        point_label, point.x + 3, point.y + text_rect.height * 3 // 4
                    )

                    # draw transparent label box
                    background_rect = text_rect.copy()
                    background_rect.x = point.x
                    background_rect.y = point.y
                    canvas.paint.color = get_fg_setting(
                        "letters_background_color"
                    ) + self.label_transparency_hx
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

                canvas.paint.color = get_fg_setting("small_letters_color")
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
            locked = self.box_config["locked"] if "locked" in self.box_config else False
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
                + f"  locked:                 {locked}\n"
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

        draw_()

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

    def lock_box_config(self, locked: bool):
        self.box_config["locked"] = locked
        self.box_config_store.save(self.box_config)
        self.redraw()

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

    def map_new_points_by_location_range(
        self, point_name, number_of_points, starting_box_number, ending_box_number
    ):
        self.reset_window_context()

        if number_of_points < 2:
            print("invalid number of points:", number_of_points)
            return

        starting_box = self.boxes[starting_box_number]
        ending_box = self.boxes[ending_box_number]

        print("box coordinates:")
        print(
            starting_box.center.x,
            starting_box.center.y,
            ending_box.center.x,
            ending_box.center.y,
        )

        self.points_map[point_name] = interpolate_points(
            starting_box.center, ending_box.center, number_of_points
        )

        self.save_points()

        self.points_showing = True
        self.redraw()

    def map_new_points_by_raw_location_range(
        self, point_name, number_of_points, starting_point, ending_point
    ):
        self.reset_window_context()

        if number_of_points < 2:
            print("invalid number of points:", number_of_points)
            return

        self.points_map[point_name] = interpolate_points(
            starting_point, ending_point, number_of_points
        )

        self.save_points()

        self.points_showing = True
        self.redraw()

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

    def find_boxes(self):
        self.reset_window_context()

        # temporarily hide everything that we have drawn so that it doesn't interfere with box detection
        self.temporarily_hide_everything()

        # use a threshold of -1 to indicate that we should scan for a good threshold
        locked = self.box_config["locked"] if "locked" in self.box_config else False
        threshold = self.box_config["threshold"] if locked else -1

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
        image_width = image_array.shape[1]
        image_height = image_array.shape[0]

        # run openCV script to find boxes in a separate process
        subprocess_args = {
            "args": (sys.executable, find_boxes_path),
            "capture_output": True,
            "input": json.dumps(
                {
                    "threshold": threshold,
                    "box_size_lower": box_size_lower,
                    "box_size_upper": box_size_upper,
                    "img": img,
                    "width": image_width,
                    "height": image_height,
                },
                separators=(",", ":"),
            ),
            "text": True,
        }

        # Add creationflags on Windows to prevent the console window from appearing
        if os.name == "nt":
            subprocess_args["creationflags"] = subprocess.CREATE_NO_WINDOW

        process = subprocess.run(**subprocess_args)

        print(process.stdout)
        print(process.stderr)

        if process.returncode != 0:
            print("Error running find_boxes.py:", process.stderr)
            return
        process_output = json.loads(process.stdout)
        boxes = process_output["boxes"]
        window_rect = ui.active_window().rect
        # TODO: needs refactoring
        self.boxes = [
            transform_image_space_to_window_space(
                image_width,
                image_height,
                window_rect,
                Rect(box["x"], box["y"], box["w"], box["h"]),
            )
            for box in boxes[::-1]
        ]
        # print("found boxes", len(self.boxes))
        self.box_config["threshold"] = process_output["threshold"]

    def go_to_box(self, box_number):
        if box_number >= len(self.boxes):
            print("box number does not exist")
            return

        box = self.boxes[box_number]
        ctrl.mouse_move(self.rect.x + box.center.x, self.rect.y + box.center.y)

        # self.boxes_showing = False
        # self.redraw()

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


# Initialize and then immediately deactivate to close the canvas
def init_and_deactivate():
    mg.setup()
    mg.deactivate()


app.register("ready", init_and_deactivate)


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

    def flex_grid_map_points_by_location_range(
        point_name: str,
        number_of_points: int,
        starting_box_number: int,
        ending_box_number: int,
    ):
        """Map points by giving a starting and ending box and number of points to interpolate"""

        mg.map_new_points_by_location_range(
            point_name, number_of_points, starting_box_number, ending_box_number
        )

    def flex_grid_map_points_by_raw_location_range(
        point_name: str,
        number_of_points: int,
        starting_x: int,
        starting_y: int,
        ending_x: int,
        ending_y: int,
    ):
        """Map points by giving a starting and ending point and number of points to interpolate"""

        mg.map_new_points_by_raw_location_range(
            point_name,
            number_of_points,
            Point2d(starting_x, starting_y),
            Point2d(ending_x, ending_y),
        )

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

    def flex_grid_boxes_threshold_view_toggle():
        """Show or hide boxes"""
        mg.toggle_boxes_threshold_view()

    def flex_grid_find_boxes():
        """Find all boxes, label with hints"""
        mg.find_boxes()

    def flex_grid_box_config_lock(onoff: int):
        """Lock box config to prevent binary search"""
        mg.lock_box_config(True if onoff == 1 else False)

    def flex_grid_go_to_box(box_number: int, mouse_button: int):
        """Go to a box"""
        mg.go_to_box(box_number)
        mg.mouse_click(mouse_button)

    def flex_grid_box_config_change(parameter: str, delta: int):
        """Change box configuration parameter by delta"""
        mg.box_config[parameter] += delta

        if parameter == "threshold":
            mg.box_config["locked"] = True

        mg.save_box_config()
        mg.find_boxes()

    def flex_grid_info_toggle():
        """Show or hide informational UI"""
        mg.toggle_info()
