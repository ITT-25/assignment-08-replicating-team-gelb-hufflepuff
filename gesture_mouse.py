import cv2
import numpy as np
from pynput.mouse import Controller, Button
import time
import pyautogui
import pygame

enable_sound = True  # wird evtl. mit args überschrieben

pygame.mixer.init()
LEFT_SOUND          = pygame.mixer.Sound("assets/leftclick.mp3")
RIGHT_SOUND         = pygame.mixer.Sound("assets/rightclick.mp3")
SCROLL_UP_SOUND     = pygame.mixer.Sound("assets/scrollup.mp3")
SCROLL_DOWN_SOUND   = pygame.mixer.Sound("assets/scrolldown.mp3")
DOUBLE_CLICK_SOUND  = pygame.mixer.Sound("assets/doubleclick.mp3")
WELCOME_SOUND       = pygame.mixer.Sound("assets/welcome.mp3")
CALIBRATING_SOUND   = pygame.mixer.Sound("assets/calibrating.mp3")

SHOW_CAM            = True
SHOW_BOUNDING_BOXES = True
SHOW_BORDER         = True
SHOW_LOG            = True

MIN_Y_DIST_FOR_SCROLL   = 10
MIN_MARKER_SIZE         = 500
BOUNDING_BOX_COLOR      = (200, 0, 0)
BOUNDING_BOX_THICKNESS  = 2
MIDPOINT_COLOR          = (0, 0, 255)
BLUE_LINE_COLOR         = (255, 0, 0)
BLUE_LINE_THICKNESS     = 2
LOG_TEXT_COLOR          = (0, 0, 0)
BORDER_COLOR            = (0, 255, 0)
BORDER_THICKNESS        = 1
BORDER_RATIO            = 0.16

screen_w, screen_h = pyautogui.size()
frame_w = frame_h = None
border_x = border_y = None
border_w = border_h = None

# ---------- Kalibrierung ----------
CALIBRATION_TIME          = 4
HSV_TOLERANCE             = np.array([15, 80, 80])
FINGER_MARKER_COLOR       = None
CALIBRATION_TEXT_COLOR    = (255, 25, 5)
CALIBRATION_CIRCLE_COLOR  = (255, 25, 5)

hand_icon = cv2.imread("assets/handicon.png", cv2.IMREAD_UNCHANGED)

prev_scroll_y = None

PINCH_CLICK_RATIO = 0.75  # value ist  inverted zum paper -> 0.25 etwas weniger als als 0.2 ausm Paper
PINCH_RESET_RATIO = 0.9
DOUBLE_CLICK_HOLD_TIME = 1.4   # paper was 5 but that seems way too long to hold
FRAMES_REQUIRED_FOR_RIGHT_CLICK = 8

logs = []
MAX_LOGS = 6

pinch_active         = False
pinch_start_area     = None
pinch_left_clicked   = False
pinch_double_clicked = False
pinch_hold_start     = None
right_click_frames   = 0

mouse     = Controller()
video_id  = 0  # kann per args gesetted werden


# ---------- Hilfsfunktionen ----------
def add_log(msg):
    global logs
    logs.append(msg)
    logs = logs[-MAX_LOGS:]

def open_cam(vid):
    cap = cv2.VideoCapture(vid)
    if not cap.isOpened():
        print("Kamera konnte nicht geöffnet werden!")
        return None
    return cap

def calculate_border(cap):
    global frame_w, frame_h, border_x, border_y, border_w, border_h
    ret, frame = cap.read()
    if not ret:
        add_log("Frame Fehler")
        return
    frame_h, frame_w, _ = frame.shape
    min_mx = int(frame_w * BORDER_RATIO)
    min_my = int(frame_h * BORDER_RATIO)
    max_w  = frame_w - 2 * min_mx
    max_h  = frame_h - 2 * min_my
    if max_w / max_h > screen_w / screen_h:
        border_h = max_h
        border_w = int(border_h * screen_w / screen_h)
    else:
        border_w = max_w
        border_h = int(border_w * screen_h / screen_w)
    border_x = (frame_w - border_w) // 2
    border_y = (frame_h - border_h) // 2

def get_mask(frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower = np.maximum(FINGER_MARKER_COLOR - HSV_TOLERANCE, [0,0,0])
    upper = np.minimum(FINGER_MARKER_COLOR + HSV_TOLERANCE, [179,255,255])
    
    mask = cv2.inRange(hsv, lower, upper)
    mask = cv2.medianBlur(mask, 5)
    mask = cv2.GaussianBlur(mask, (7, 7), 0)

    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask

def map_to_screen(x, y):
    uw = frame_w - 2 * border_x
    uh = frame_h - 2 * border_y
    x = np.clip(x, border_x, frame_w - border_x)
    y = np.clip(y, border_y, frame_h - border_y)
    return int((x - border_x) / uw * screen_w), int((y - border_y) / uh * screen_h)

def draw_hand_icon(frame, icon, center):
    if icon is None or icon.shape[2] < 4:
        return
    ih, iw = icon.shape[:2]
    x = center[0] - iw // 2
    y = center[1] - ih // 2

    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(frame.shape[1], x + iw), min(frame.shape[0], y + ih)

    icon_roi = icon[y1 - y:y2 - y, x1 - x:x2 - x]
    alpha = icon_roi[:, :, 3] / 255.0
    for c in range(3):
        frame[y1:y2, x1:x2, c] = (1 - alpha) * frame[y1:y2, x1:x2, c] + alpha * icon_roi[:, :, c]


# ---------- Hauptschleife ----------
def detect_fingers(cap):
    global FINGER_MARKER_COLOR, prev_scroll_y
    global pinch_active, pinch_start_area, pinch_left_clicked
    global pinch_double_clicked, pinch_hold_start, right_click_frames

    calibrating = False
    cal_start   = None
    cal_samples = []

    while True:
        logs.clear()
        ret, frame = cap.read()
        if not ret:
            add_log("Frame Fehler")
            break
        frame = cv2.flip(frame, 1)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        if key == ord('k'):
            calibrating  = True
            cal_start    = time.time()
            cal_samples  = []
            if enable_sound:
                CALIBRATING_SOUND.play()

        # ---------- Kalibrierung ----------
        if calibrating:
            cx, cy = frame_w // 2, frame_h // 2
            draw_hand_icon(frame, hand_icon, (cx, cy))
            cv2.circle(frame, (cx, cy), 13, CALIBRATION_CIRCLE_COLOR, 3)

            patch  = frame[cy - 2:cy + 3, cx - 2:cx + 3]
            hsv_p  = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
            cal_samples.append((time.time(), np.mean(hsv_p.reshape(-1, 3), 0)))
            if time.time() - cal_start >= CALIBRATION_TIME:
                recent = [s for t, s in cal_samples if t >= cal_start + CALIBRATION_TIME * 0.66]
                FINGER_MARKER_COLOR = np.mean(recent or [s for _, s in cal_samples], 0).astype(int)
                calibrating = False
                add_log("Kalibrierung abgeschlossen")

        mask = get_mask(frame) if FINGER_MARKER_COLOR is not None and not calibrating else None
        fingertips = []
        if mask is not None:
            cnts, _ = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            for c in cnts:
                if cv2.contourArea(c) > MIN_MARKER_SIZE:
                    x, y, w, h = cv2.boundingRect(c)
                    fingertips.append({'center': (x + w // 2, y + h // 2), 'bbox': (x, y, w, h)})
                    if SHOW_BOUNDING_BOXES:
                        cv2.rectangle(frame, (x, y), (x + w, y + h), BOUNDING_BOX_COLOR, BOUNDING_BOX_THICKNESS)

        # ---------- Rechtsklick ----------
        if len(fingertips) == 1:
            right_click_frames += 1
            if right_click_frames == FRAMES_REQUIRED_FOR_RIGHT_CLICK:
                mouse.click(Button.right, 1)
                if enable_sound:
                    RIGHT_SOUND.play()
                add_log("Rechtsklick")
        else:
            right_click_frames = 0

        # ---------- Linksklick ----------
        if len(fingertips) == 2:
            xs = [f['center'][0] for f in fingertips]
            ys = [f['center'][1] for f in fingertips]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            w, h = max_x - min_x, max_y - min_y
            area = w * h if w and h else 1
            mid_x, mid_y = (min_x + max_x) // 2, (min_y + max_y) // 2
            if SHOW_BOUNDING_BOXES:
                cv2.circle(frame, (mid_x, mid_y), 5, MIDPOINT_COLOR, -1)
                for f in fingertips:
                    cv2.line(frame, f['center'], (mid_x, mid_y),
                             BLUE_LINE_COLOR, BLUE_LINE_THICKNESS)

            mouse.position = map_to_screen(mid_x, mid_y)

            if not pinch_active:
                pinch_active = True
                pinch_start_area = area
                pinch_left_clicked = False
                pinch_double_clicked = False
                pinch_hold_start = None
                add_log("Pinch-Start")
            else:
                ratio = area / pinch_start_area if pinch_start_area else 1
                #add_log(f"Pinch-Ratio: {ratio:.2f}")
                if ratio <= PINCH_CLICK_RATIO and not pinch_left_clicked:
                    mouse.click(Button.left, 1)
                    if enable_sound:
                        LEFT_SOUND.play()
                    pinch_left_clicked = True
                    pinch_hold_start = time.time()
                    add_log("Linksklick")
                if pinch_left_clicked and not pinch_double_clicked and \
                   time.time() - pinch_hold_start >= DOUBLE_CLICK_HOLD_TIME:
                    mouse.click(Button.left, 2)
                    if enable_sound:
                        DOUBLE_CLICK_SOUND.play()
                    pinch_double_clicked = True
                    add_log("Doppelklick")
                if ratio >= PINCH_RESET_RATIO:
                    pinch_active = False
                    add_log("Pinch-Reset")
        else:
            pinch_active = False

        # ---------- Scroll ----------
        if len(fingertips) == 3:
            avg_y = sum(f['center'][1] for f in fingertips) // 3
            if prev_scroll_y is not None:
                dy = avg_y - prev_scroll_y
                if abs(dy) > MIN_Y_DIST_FOR_SCROLL:
                    mouse.scroll(0, -1 if dy > 0 else 1)
                    if enable_sound:
                        (SCROLL_DOWN_SOUND if dy > 0 else SCROLL_UP_SOUND).play()
                    add_log("Scroll runter" if dy > 0 else "Scroll rauf")
            prev_scroll_y = avg_y
        else:
            prev_scroll_y = None

        # ---------- UI Overlays ----------
        if FINGER_MARKER_COLOR is None and not calibrating:
            cv2.putText(frame,
                        "To begin, press 'k' to calibrate or 'q' to quit",
                        (30, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.79, CALIBRATION_TEXT_COLOR, 2)
        if FINGER_MARKER_COLOR is not None:
            bgr = cv2.cvtColor(np.uint8([[FINGER_MARKER_COLOR]]), cv2.COLOR_HSV2BGR)[0][0]
            cv2.circle(frame, (frame_w - 20, 20), 10, bgr.tolist(), -1)
        if SHOW_BORDER:
            cv2.rectangle(frame, (border_x, border_y),(border_x + border_w, border_y + border_h),BORDER_COLOR, BORDER_THICKNESS)
        if SHOW_LOG:
            for i, txt in enumerate(reversed(logs)):
                y = frame_h - 20 - i * 20
                cv2.putText(frame, txt, (20, y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, LOG_TEXT_COLOR, 1)

        if SHOW_CAM:
            cv2.imshow("Gesture_Mouse", frame)

    cap.release()
    cv2.destroyAllWindows()


def main():
    cap = open_cam(video_id)
    if cap:
        if enable_sound:
            WELCOME_SOUND.play()
        calculate_border(cap)
        detect_fingers(cap)



if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--video_id", type=int, default=0, help="Kamera ID (Default 0)")
    parser.add_argument("-s", "--enable_sounds", type=int, choices=[0, 1], default=1, help="1: Sounds an, 0: aus")
    args = parser.parse_args()

    video_id = args.video_id
    enable_sound = bool(args.enable_sounds)

    main()
