"""Unity Mecanim humanoid trait tables (engine-authoritative constants).

These are the values of UnityEngine.HumanTrait for the humanoid muscle
space: the (bone, axis) -> muscle index mapping and each muscle's default
rotation limits in degrees. Humanoid avatars built with
`HumanDescription.useDefaultValues == true` (verified for the target game's
avatars) use exactly these limits.
"""

# (HumanTrait bone name) -> (muscleX, muscleY, muscleZ); -1 = no channel.
# Axis order is (Twist, In-Out/Left-Right, Front-Back/Down-Up).
BONE_MUSCLES = {
    'Hips': (-1, -1, -1),
    'LeftUpperLeg': (23, 22, 21),
    'RightUpperLeg': (31, 30, 29),
    'LeftLowerLeg': (25, -1, 24),
    'RightLowerLeg': (33, -1, 32),
    'LeftFoot': (-1, 27, 26),
    'RightFoot': (-1, 35, 34),
    'Spine': (2, 1, 0),
    'Chest': (5, 4, 3),
    'Neck': (11, 10, 9),
    'Head': (14, 13, 12),
    'LeftShoulder': (-1, 38, 37),
    'RightShoulder': (-1, 47, 46),
    'LeftUpperArm': (41, 40, 39),
    'RightUpperArm': (50, 49, 48),
    'LeftLowerArm': (43, -1, 42),
    'RightLowerArm': (52, -1, 51),
    'LeftHand': (-1, 45, 44),
    'RightHand': (-1, 54, 53),
    'LeftToes': (-1, -1, 28),
    'RightToes': (-1, -1, 36),
    'LeftEye': (-1, 16, 15),
    'RightEye': (-1, 18, 17),
    'Jaw': (-1, 20, 19),
    'Left Thumb Proximal': (-1, 56, 55),
    'Left Thumb Intermediate': (-1, -1, 57),
    'Left Thumb Distal': (-1, -1, 58),
    'Left Index Proximal': (-1, 60, 59),
    'Left Index Intermediate': (-1, -1, 61),
    'Left Index Distal': (-1, -1, 62),
    'Left Middle Proximal': (-1, 64, 63),
    'Left Middle Intermediate': (-1, -1, 65),
    'Left Middle Distal': (-1, -1, 66),
    'Left Ring Proximal': (-1, 68, 67),
    'Left Ring Intermediate': (-1, -1, 69),
    'Left Ring Distal': (-1, -1, 70),
    'Left Little Proximal': (-1, 72, 71),
    'Left Little Intermediate': (-1, -1, 73),
    'Left Little Distal': (-1, -1, 74),
    'Right Thumb Proximal': (-1, 76, 75),
    'Right Thumb Intermediate': (-1, -1, 77),
    'Right Thumb Distal': (-1, -1, 78),
    'Right Index Proximal': (-1, 80, 79),
    'Right Index Intermediate': (-1, -1, 81),
    'Right Index Distal': (-1, -1, 82),
    'Right Middle Proximal': (-1, 84, 83),
    'Right Middle Intermediate': (-1, -1, 85),
    'Right Middle Distal': (-1, -1, 86),
    'Right Ring Proximal': (-1, 88, 87),
    'Right Ring Intermediate': (-1, -1, 89),
    'Right Ring Distal': (-1, -1, 90),
    'Right Little Proximal': (-1, 92, 91),
    'Right Little Intermediate': (-1, -1, 93),
    'Right Little Distal': (-1, -1, 94),
    'UpperChest': (8, 7, 6),
}

# muscle index -> (default min deg, default max deg)
MUSCLE_LIMITS = {
    0: (-40, 40),          # Spine Front-Back
    1: (-40, 40),          # Spine Left-Right
    2: (-40, 40),          # Spine Twist Left-Right
    3: (-40, 40),          # Chest Front-Back
    4: (-40, 40),          # Chest Left-Right
    5: (-40, 40),          # Chest Twist Left-Right
    6: (-20, 20),          # UpperChest Front-Back
    7: (-20, 20),          # UpperChest Left-Right
    8: (-20, 20),          # UpperChest Twist Left-Right
    9: (-40, 40),          # Neck Nod Down-Up
    10: (-40, 40),          # Neck Tilt Left-Right
    11: (-40, 40),          # Neck Turn Left-Right
    12: (-40, 40),          # Head Nod Down-Up
    13: (-40, 40),          # Head Tilt Left-Right
    14: (-40, 40),          # Head Turn Left-Right
    15: (-10, 15),          # Left Eye Down-Up
    16: (-20, 20),          # Left Eye In-Out
    17: (-10, 15),          # Right Eye Down-Up
    18: (-20, 20),          # Right Eye In-Out
    19: (-10, 10),          # Jaw Close
    20: (-10, 10),          # Jaw Left-Right
    21: (-90, 50),          # Left Upper Leg Front-Back
    22: (-60, 60),          # Left Upper Leg In-Out
    23: (-60, 60),          # Left Upper Leg Twist In-Out
    24: (-80, 80),          # Left Lower Leg Stretch
    25: (-90, 90),          # Left Lower Leg Twist In-Out
    26: (-50, 50),          # Left Foot Up-Down
    27: (-30, 30),          # Left Foot Twist In-Out
    28: (-50, 50),          # Left Toes Up-Down
    29: (-90, 50),          # Right Upper Leg Front-Back
    30: (-60, 60),          # Right Upper Leg In-Out
    31: (-60, 60),          # Right Upper Leg Twist In-Out
    32: (-80, 80),          # Right Lower Leg Stretch
    33: (-90, 90),          # Right Lower Leg Twist In-Out
    34: (-50, 50),          # Right Foot Up-Down
    35: (-30, 30),          # Right Foot Twist In-Out
    36: (-50, 50),          # Right Toes Up-Down
    37: (-15, 30),          # Left Shoulder Down-Up
    38: (-15, 15),          # Left Shoulder Front-Back
    39: (-60, 100),          # Left Arm Down-Up
    40: (-100, 100),          # Left Arm Front-Back
    41: (-90, 90),          # Left Arm Twist In-Out
    42: (-80, 80),          # Left Forearm Stretch
    43: (-90, 90),          # Left Forearm Twist In-Out
    44: (-80, 80),          # Left Hand Down-Up
    45: (-40, 40),          # Left Hand In-Out
    46: (-15, 30),          # Right Shoulder Down-Up
    47: (-15, 15),          # Right Shoulder Front-Back
    48: (-60, 100),          # Right Arm Down-Up
    49: (-100, 100),          # Right Arm Front-Back
    50: (-90, 90),          # Right Arm Twist In-Out
    51: (-80, 80),          # Right Forearm Stretch
    52: (-90, 90),          # Right Forearm Twist In-Out
    53: (-80, 80),          # Right Hand Down-Up
    54: (-40, 40),          # Right Hand In-Out
    55: (-20, 20),          # Left Thumb 1 Stretched
    56: (-25, 25),          # Left Thumb Spread
    57: (-40, 35),          # Left Thumb 2 Stretched
    58: (-40, 35),          # Left Thumb 3 Stretched
    59: (-50, 50),          # Left Index 1 Stretched
    60: (-20, 20),          # Left Index Spread
    61: (-45, 45),          # Left Index 2 Stretched
    62: (-45, 45),          # Left Index 3 Stretched
    63: (-50, 50),          # Left Middle 1 Stretched
    64: (-7.5, 7.5),          # Left Middle Spread
    65: (-45, 45),          # Left Middle 2 Stretched
    66: (-45, 45),          # Left Middle 3 Stretched
    67: (-50, 50),          # Left Ring 1 Stretched
    68: (-7.5, 7.5),          # Left Ring Spread
    69: (-45, 45),          # Left Ring 2 Stretched
    70: (-45, 45),          # Left Ring 3 Stretched
    71: (-50, 50),          # Left Little 1 Stretched
    72: (-20, 20),          # Left Little Spread
    73: (-45, 45),          # Left Little 2 Stretched
    74: (-45, 45),          # Left Little 3 Stretched
    75: (-20, 20),          # Right Thumb 1 Stretched
    76: (-25, 25),          # Right Thumb Spread
    77: (-40, 35),          # Right Thumb 2 Stretched
    78: (-40, 35),          # Right Thumb 3 Stretched
    79: (-50, 50),          # Right Index 1 Stretched
    80: (-20, 20),          # Right Index Spread
    81: (-45, 45),          # Right Index 2 Stretched
    82: (-45, 45),          # Right Index 3 Stretched
    83: (-50, 50),          # Right Middle 1 Stretched
    84: (-7.5, 7.5),          # Right Middle Spread
    85: (-45, 45),          # Right Middle 2 Stretched
    86: (-45, 45),          # Right Middle 3 Stretched
    87: (-50, 50),          # Right Ring 1 Stretched
    88: (-7.5, 7.5),          # Right Ring Spread
    89: (-45, 45),          # Right Ring 2 Stretched
    90: (-45, 45),          # Right Ring 3 Stretched
    91: (-50, 50),          # Right Little 1 Stretched
    92: (-20, 20),          # Right Little Spread
    93: (-45, 45),          # Right Little 2 Stretched
    94: (-45, 45),          # Right Little 3 Stretched
}

# Game-skeleton bone name -> HumanTrait bone name (verified against the
# avatars' own limit tables). Fingers use two joints per finger.
GAME_TO_HUMAN = {
    "Hips": "Hips", "Spine": "Spine", "Spine1": "Chest", "Head": "Head",
    "LeftUpLeg": "LeftUpperLeg", "RightUpLeg": "RightUpperLeg",
    "LeftLeg": "LeftLowerLeg", "RightLeg": "RightLowerLeg",
    "LeftFoot": "LeftFoot", "RightFoot": "RightFoot",
    "LeftArm": "LeftUpperArm", "RightArm": "RightUpperArm",
    "LeftForeArm": "LeftLowerArm", "RightForeArm": "RightLowerArm",
    "LeftHand": "LeftHand", "RightHand": "RightHand",
}
for _side in ("Left", "Right"):
    for _g, _m in (("Thumb", "Thumb"), ("Index", "Index"), ("Middle", "Middle"),
                   ("Ring", "Ring"), ("Pinky", "Little")):
        GAME_TO_HUMAN[f"{_side}Hand{_g}1"] = f"{_side} {_m} Proximal"
        GAME_TO_HUMAN[f"{_side}Hand{_g}2"] = f"{_side} {_m} Intermediate"

# Translation-DoF slots (clip curve attributes ``137 + 3*slot``), in the
# engine's fixed 21-slot order.  Slots verified against Animator playback on
# real content: 0 1 4 6 7 10 11 14 16 18 20 (every slot the shipped library
# uses; worst |predicted - engine| local translation 3.4e-5).  The value is a
# humanoid-normalized offset; the host bone's local translation becomes
# ``rest - humanScale * (v.x, v.z, v.y)`` (identical component order on every
# verified host, i.e. an engine-fixed convention, not a per-bone axes remap).
TDOF_BONES = ("Spine", "Chest", "UpperChest", "Neck", "Head",
              "LeftUpperLeg", "LeftLowerLeg", "LeftFoot", "LeftToes",
              "RightUpperLeg", "RightLowerLeg", "RightFoot", "RightToes",
              "LeftShoulder", "LeftUpperArm", "LeftLowerArm", "LeftHand",
              "RightShoulder", "RightUpperArm", "RightLowerArm", "RightHand")
HUMAN_TO_GAME = {h: g for g, h in GAME_TO_HUMAN.items()}

# Twist redistribution: which avatar parameter scales a bone's own twist,
# and which child bone inherits the remainder.
TWIST_PARAM = {"LeftUpLeg": "upperLegTwist", "RightUpLeg": "upperLegTwist",
               "LeftLeg": "legTwist", "RightLeg": "legTwist",
               "LeftArm": "armTwist", "RightArm": "armTwist",
               "LeftForeArm": "foreArmTwist", "RightForeArm": "foreArmTwist"}
TWIST_CHILD = {"LeftUpLeg": "LeftLeg", "RightUpLeg": "RightLeg",
               "LeftLeg": "LeftFoot", "RightLeg": "RightFoot",
               "LeftArm": "LeftForeArm", "RightArm": "RightForeArm",
               "LeftForeArm": "LeftHand", "RightForeArm": "RightHand"}
TWIST_PARENT = {v: k for k, v in TWIST_CHILD.items()}

# Muscles of humanoid bones the skeleton does not have fold into the host
# bone's matching axes, using the missing bone's own limits. On official
# content these channels are constant zero (exporters never write DoF for
# bones absent from the rig), so folding is kept for completeness only.
FOLD_INTO = {"Head": ("Neck", (11, 10, 9)),
             "Spine1": ("UpperChest", (8, 7, 6))}
