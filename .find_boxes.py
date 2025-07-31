import sys
try:
    import cv2
except ImportError:
    print("Python module 'opencv-python-headless' not found. Can not search for boxes. Check flex-grid readme for installation instructions")
import numpy as np
import json
import base64


# TODO: rewrite this without talon.skia.Image dependency
# def view_image(image_array, name):
#     # open the image (macOS only)
#     Image.from_array(image_array).write_file(f"/tmp/{name}.jpg")
#     subprocess.run(("open", f"/tmp/{name}.jpg"))


class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y


class Rect:
    def __init__(self, x, y, w, h):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.center = Point(x + w / 2, y + h / 2)


class RectEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Rect):
            out_dict = obj.__dict__
            del out_dict["center"]
            return out_dict
        return json.JSONEncoder.default(self, obj)


def find_boxes_at_best_threshold(box_size_lower, box_size_upper, img):
    results = {}

    def find_maximum(function, lower, upper, iterations_left):
        middle = int((upper + lower) / 2)
        result = len(function(middle))
        results[middle] = result

        # short circuit when out of iterations or results are all the same
        if iterations_left == 0 or (results[lower] == result == results[upper]):
            return middle

        # handle triangle case, e.g. 4, 10, 6
        if results[lower] < result > results[upper]:
            if results[lower] > results[upper]:
                return find_maximum(function, lower, middle, iterations_left - 1)
            else:
                return find_maximum(function, middle, upper, iterations_left - 1)

        if result > results[lower]:
            return find_maximum(function, middle, upper, iterations_left - 1)
        else:
            return find_maximum(function, lower, middle, iterations_left - 1)

    def find_boxes_at_threshold(threshold):
        return find_boxes(threshold, box_size_lower, box_size_upper, img)

    # first do a broad scan, checking number of boxes found across a range of thresholds
    results = {
        threshold: len(find_boxes_at_threshold(threshold))
        for threshold in range(5, 256, 25)
    }

    lower = 5
    upper = 5
    upper_result = results[5]
    # iterate up threshold values. when a new max is found, store threshold as upper. old upper
    # becomes lower.
    for threshold, result in results.items():
        if result >= upper_result:
            upper_result = result
            lower = upper
            upper = threshold

    final_threshold = find_maximum(find_boxes_at_threshold, lower, upper, 4)

    return final_threshold, find_boxes_at_threshold(final_threshold)


def find_boxes(threshold, box_size_lower, box_size_upper, img):
    # find boxes by first applying a threshold filter to the grayscale window
    _, thresh = cv2.threshold(img, threshold, 255, cv2.THRESH_BINARY)
    # view_image(thresh, "thresh")

    # use a close morphology transform to filter out thin lines
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 1))
    morph = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
    # view_image(morph, "morph")

    # now search all of the contours for small square-ish things
    contours, _ = cv2.findContours(morph, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    all_boxes = []
    for c in contours:
        (x, y, w, h) = cv2.boundingRect(c)
        if (
            (w >= box_size_lower and w < box_size_upper)
            and (h > box_size_lower and h < box_size_upper)
            and abs(w - h) < 0.8 * w
        ):
            all_boxes.append(Rect(x, y, w, h))

    # print("found boxes", len(all_boxes))

    # filter boxes that are too similar to each other
    boxes = []
    for i, box1 in enumerate(all_boxes):
        omit = False
        for j in range(i + 1, len(all_boxes)):
            box2 = all_boxes[j]
            box1center = box1.center
            box2center = box2.center
            if (
                abs(box1center.x - box2center.x) < box_size_lower
                and abs(box1center.y - box2center.y) < box_size_lower
            ):
                # omit this box since its center is nearby another box's center
                omit = True
                break

        if not omit:
            boxes.append(box1)

    # print("after omissions", len(boxes))

    return boxes


if __name__ == "__main__":
    args = json.load(sys.stdin)

    # convert base64 string to numpy array
    img_b64 = base64.b64decode(args["img"])
    img = np.frombuffer(img_b64, dtype=np.uint8)

    # reshape array to image dimensions
    img = img.reshape(args["height"], args["width"], 3)

    # convert to grayscale
    img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    threshold = args["threshold"]
    if threshold >= 0:
        boxes = find_boxes(
            threshold, args["box_size_lower"], args["box_size_upper"], img
        )
    else:
        threshold, boxes = find_boxes_at_best_threshold(
            args["box_size_lower"], args["box_size_upper"], img
        )

    # print output as json
    output = {"boxes": boxes, "threshold": threshold}
    print(json.dumps(output, cls=RectEncoder, separators=(",", ":")))
