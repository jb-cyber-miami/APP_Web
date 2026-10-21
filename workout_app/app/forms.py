"""Server-side validation for every form. Client-side checks are only for UX."""

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    IntegerField,
    PasswordField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
    widgets,
)
from wtforms.validators import (
    URL,
    DataRequired,
    EqualTo,
    Length,
    NumberRange,
    Optional,
    Regexp,
    ValidationError,
)

from .services.youtube import parse_video_id
from .timeutil import is_valid_timezone

USERNAME_RE = r"^[A-Za-z0-9_.-]+$"

GYM_SIZES = [
    ("home", "Home gym / apartment"),
    ("small", "Small gym / hotel gym"),
    ("commercial", "Full commercial gym"),
]
EQUIPMENT = [
    ("bodyweight", "Bodyweight only"),
    ("dumbbells", "Dumbbells"),
    ("barbell", "Barbell + plates"),
    ("squat_rack", "Squat rack / power rack"),
    ("bench", "Adjustable bench"),
    ("cable_machine", "Cable machine"),
    ("smith_machine", "Smith machine"),
    ("leg_press", "Leg press"),
    ("machines", "Selectorized machines"),
    ("kettlebells", "Kettlebells"),
    ("pull_up_bar", "Pull-up bar"),
    ("resistance_bands", "Resistance bands"),
    ("treadmill", "Treadmill"),
    ("bike", "Stationary bike"),
    ("rower", "Rowing machine"),
    ("trx", "TRX / suspension trainer"),
]
GOALS = [
    ("general fitness", "General fitness"),
    ("muscle gain", "Build muscle"),
    ("strength", "Get stronger"),
    ("fat loss", "Lose fat"),
    ("endurance", "Endurance"),
]
LEVELS = [("beginner", "Beginner"), ("intermediate", "Intermediate"), ("advanced", "Advanced")]
FOCUS = [
    ("full body", "Full body"),
    ("upper body", "Upper body"),
    ("lower body", "Lower body"),
    ("push (chest, shoulders, triceps)", "Push"),
    ("pull (back, biceps)", "Pull"),
    ("legs and glutes", "Legs & glutes"),
    ("core", "Core"),
    ("conditioning / cardio", "Conditioning"),
]


class MultiCheckboxField(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


class RegisterForm(FlaskForm):
    username = StringField(
        "Username",
        validators=[
            DataRequired(),
            Length(3, 32),
            Regexp(USERNAME_RE, message="Letters, numbers, dot, dash and underscore only."),
        ],
    )
    display_name = StringField("Name (shown to friends)", validators=[Optional(), Length(max=64)])
    password = PasswordField("Password", validators=[DataRequired(), Length(10, 128)])
    confirm = PasswordField(
        "Confirm password", validators=[DataRequired(), EqualTo("password", "Passwords must match.")]
    )

    def validate_password(self, field):
        if self.username.data and field.data.lower() == self.username.data.lower():
            raise ValidationError("Password can't be the same as your username.")
        if len(set(field.data)) < 5:
            raise ValidationError("Password is too simple.")


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=32)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])
    remember = BooleanField("Keep me signed in")


class ChangePasswordForm(FlaskForm):
    current = PasswordField("Current password", validators=[Optional(), Length(max=128)])
    password = PasswordField("New password", validators=[DataRequired(), Length(10, 128)])
    confirm = PasswordField(
        "Confirm new password", validators=[DataRequired(), EqualTo("password", "Passwords must match.")]
    )


class SettingsForm(FlaskForm):
    display_name = StringField("Name (shown to friends)", validators=[DataRequired(), Length(max=64)])
    timezone = StringField("Timezone", validators=[DataRequired(), Length(max=64)])

    def validate_timezone(self, field):
        if not is_valid_timezone(field.data):
            raise ValidationError("Unknown timezone. Example: America/New_York")


class ExerciseForm(FlaskForm):
    name = StringField("Exercise name", validators=[DataRequired(), Length(max=80)])
    muscle_group = StringField("Muscle group", validators=[Optional(), Length(max=40)])
    equipment = StringField("Equipment", validators=[Optional(), Length(max=60)])
    sets = IntegerField("Sets", default=3, validators=[DataRequired(), NumberRange(1, 20)])
    reps = StringField(
        "Reps (e.g. 10 or 8-12 or 30s)", default="10", validators=[DataRequired(), Length(max=20)]
    )
    rest_seconds = IntegerField("Rest (seconds)", default=60, validators=[Optional(), NumberRange(0, 600)])
    instructions = TextAreaField("How to do it", validators=[Optional(), Length(max=2000)])
    youtube_url = StringField(
        "YouTube video link (optional)", validators=[Optional(), Length(max=200), URL(require_tld=True)]
    )

    def validate_youtube_url(self, field):
        if field.data and not parse_video_id(field.data):
            raise ValidationError("That doesn't look like a YouTube video link.")


class GymProfileForm(FlaskForm):
    gym_size = SelectField("Gym size", choices=GYM_SIZES)
    equipment = MultiCheckboxField("Equipment available", choices=EQUIPMENT)
    goal = SelectField("Main goal", choices=GOALS)
    experience = SelectField("Experience", choices=LEVELS)
    notes = TextAreaField(
        "Anything else (injuries, limitations, preferences)", validators=[Optional(), Length(max=500)]
    )


class AIGenerateForm(FlaskForm):
    focus = SelectField("Focus", choices=FOCUS)
    count = IntegerField("How many exercises", default=6, validators=[DataRequired(), NumberRange(3, 10)])
    minutes = IntegerField(
        "Workout length (minutes)", default=45, validators=[DataRequired(), NumberRange(10, 120)]
    )
    extra = StringField("Extra request (optional)", validators=[Optional(), Length(max=300)])


class EntryForm(FlaskForm):
    exercise_id = SelectField("Exercise", coerce=int, validators=[DataRequired()])
    sets_done = IntegerField("Sets", default=3, validators=[DataRequired(), NumberRange(1, 50)])
    reps_done = StringField("Reps", validators=[Optional(), Length(max=20)])
    weight = StringField("Weight", validators=[Optional(), Length(max=20)])


class ManualCaloriesForm(FlaskForm):
    calories = IntegerField("Calories burned", validators=[DataRequired(), NumberRange(0, 10000)])


class ApiTokenForm(FlaskForm):
    name = StringField("Token name", default="iPhone Shortcut", validators=[DataRequired(), Length(max=40)])


class ShareForm(FlaskForm):
    day = DateField("Day", validators=[DataRequired()])
