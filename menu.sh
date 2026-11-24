#!/usr/bin/env bash
# pixel-board interactive menu: pick a number, no idf.py commands to remember.
set -u

readonly IDF_ACTIVATE="${IDF_ACTIVATE:-/home/user/.espressif/tools/activate_idf_v5.5.6.sh}"
readonly PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PORT_PATTERN='^/dev/tty(ACM|USB)[0-9]+$'

cd "$PROJECT_DIR" || { echo "Cannot enter project dir: $PROJECT_DIR" >&2; exit 1; }

load_idf() {
    if [[ -n "${IDF_PATH:-}" && "${IDF_PATH}" == *v5.5* ]]; then
        return 0
    fi
    if [[ ! -f "$IDF_ACTIVATE" ]]; then
        echo "ESP-IDF v5.5 activation script not found: $IDF_ACTIVATE" >&2
        echo "Install ESP-IDF v5.5.x with EIM, or set IDF_ACTIVATE to its activate_idf_*.sh" >&2
        return 1
    fi
    # Sourcing the EIM script from another script makes it `exit 1` (it only detects being
    # sourced from an interactive bash/sh), so use its `-e` mode, which prints KEY=VALUE lines.
    local env_out key value
    if ! env_out="$(bash "$IDF_ACTIVATE" -e)"; then
        echo "Failed to read ESP-IDF environment from $IDF_ACTIVATE" >&2
        return 1
    fi
    while IFS= read -r line; do
        [[ "$line" == *=* ]] || continue
        key="${line%%=*}"
        value="${line#*=}"
        [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
        case "$key" in
            SYSTEM_PATH) ;;                         # snapshot of PATH at install time, ignore
            PATH) export PATH="$value:$PATH" ;;     # ESP-IDF tool dirs only
            *) export "$key=$value" ;;
        esac
    done <<< "$env_out"
    if [[ -z "${IDF_PATH:-}" || ! -d "$IDF_PATH" ]]; then
        echo "ESP-IDF environment from $IDF_ACTIVATE has no valid IDF_PATH" >&2
        return 1
    fi
    echo "ESP-IDF: $IDF_PATH"
}

idf() {
    python "$IDF_PATH/tools/idf.py" "$@"
}

pick_port() {
    local ports=()
    local p
    for p in /dev/ttyACM* /dev/ttyUSB*; do
        [[ -e "$p" && "$p" =~ $PORT_PATTERN ]] && ports+=("$p")
    done
    if [[ ${#ports[@]} -eq 0 ]]; then
        echo "No serial port found (/dev/ttyACM* or /dev/ttyUSB*)." >&2
        echo "On WSL, attach the board first: usbipd list / usbipd attach --wsl --busid <BUSID> (Windows PowerShell)" >&2
        return 1
    fi
    if [[ ${#ports[@]} -eq 1 ]]; then
        PORT="${ports[0]}"
    else
        echo "Select serial port:"
        select p in "${ports[@]}"; do
            if [[ -n "${p:-}" ]]; then PORT="$p"; break; fi
            echo "Invalid choice"
        done
    fi
    echo "Port: $PORT"
}

run_step() {
    local label="$1"; shift
    echo "==> $label"
    if "$@"; then
        echo "==> $label: OK"
        return 0
    fi
    echo "==> $label: FAILED" >&2
    return 1
}

do_build()   { load_idf && run_step "Build" idf build; }
do_flash()   { load_idf && pick_port && run_step "Flash" idf -p "$PORT" flash; }
do_monitor() { load_idf && pick_port && echo "(Exit monitor with Ctrl+])" && idf -p "$PORT" monitor; }
do_all()     { load_idf && pick_port && run_step "Build" idf build && run_step "Flash" idf -p "$PORT" flash && echo "(Exit monitor with Ctrl+])" && idf -p "$PORT" monitor; }
do_rebuild() { load_idf && run_step "Full clean" idf fullclean && run_step "Build" idf build; }
do_tests() {
    local out
    out="$(mktemp)" || return 1
    run_step "Host unit tests (font)" bash -c \
        'g++ -std=c++17 -Wall -Wextra -Werror -I main test/host/test_font.cpp main/font5x7.cpp -o "$1" && "$1"' _ "$out"
    local rc=$?
    if [[ $rc -eq 0 ]]; then
        run_step "Host unit tests (tone)" bash -c \
            'g++ -std=c++17 -Wall -Wextra -Werror -I main test/host/test_tone.cpp main/tone.cpp -o "$1" && "$1"' _ "$out"
        rc=$?
    fi
    if [[ $rc -eq 0 ]]; then
        run_step "Host unit tests (media)" bash -c \
            'g++ -std=c++17 -Wall -Wextra -Werror -I main test/host/test_media.cpp main/media.cpp -o "$1" && "$1"' _ "$out"
        rc=$?
    fi
    rm -f "$out"
    [[ $rc -eq 0 ]] || return $rc
    run_step "Host unit tests (gif2c)" python3 -m unittest discover -s test/host -p 'test_*.py' || return 1
    ensure_studio_venv || return 1
    run_step "Studio unit tests" "$STUDIO_PY" -m unittest discover -s studio/tests -t studio
}

# Test venv for studio/: system Python + Pillow, plus the pinned esptool/pyserial.
readonly STUDIO_PY="$PROJECT_DIR/.venv-studio/bin/python"
ensure_studio_venv() {
    [[ -x "$STUDIO_PY" ]] && return 0
    echo "==> Creating .venv-studio (first run only)"
    python3 -m venv --system-site-packages "$PROJECT_DIR/.venv-studio" &&
        "$STUDIO_PY" -m pip install -q esptool==5.6.0 pyserial==3.5 || {
            echo "Cannot create .venv-studio" >&2
            return 1
        }
}

# Reads `constexpr ... <name> = <number>;` from main/board_config.h into the named variable.
read_board_value() {
    local name="$1" value
    value="$(sed -n "s/^constexpr [a-z0-9_]* ${name} = \([0-9]\+\);.*/\1/p" main/board_config.h)"
    if [[ ! "$value" =~ ^[0-9]+$ ]] || (( value < 1 || value > 256 )); then
        echo "Cannot read ${name} from main/board_config.h (got '${value}')" >&2
        return 1
    fi
    printf '%s' "$value"
}

# Pick a GIF from assets/gif/ and regenerate main/gif_anim_data.cpp (then Build + flash).
do_convert_gif() {
    local gifs=() g pw ph chain split=1
    pw="$(read_board_value kPanelWidth)" || return 1
    ph="$(read_board_value kPanelHeight)" || return 1
    chain="$(read_board_value kPanelChain)" || return 1
    for g in assets/gif/*.gif; do
        [[ -f "$g" ]] && gifs+=("$g")
    done
    if [[ ${#gifs[@]} -eq 0 ]]; then
        echo "No .gif files in assets/gif/" >&2
        return 1
    fi
    echo "Select GIF:"
    select g in "${gifs[@]}"; do
        if [[ -n "${g:-}" ]]; then break; fi
        echo "Invalid choice"
    done
    if (( chain > 1 )); then
        local layouts=("Split: one part per panel (e.g. a pair of eyes)" "Whole GIF stretched across all ${chain} panels")
        echo "Layout for ${chain} chained panels:"
        select _ in "${layouts[@]}"; do
            case "$REPLY" in
                1) split="$chain"; break ;;
                2) split=1; break ;;
                *) echo "Invalid choice" ;;
            esac
        done
    fi
    run_step "Convert $g" python3 tools/gif2c.py "$g" main/gif_anim_data.cpp \
        --width $(( pw * chain )) --height "$ph" --split "$split"
}

do_build_studio() { do_build && run_step "Build Studio exe" bash studio/build_exe.sh; }

options=(
    "Build"
    "Flash"
    "Serial log (monitor)"
    "Build + flash + serial log"
    "Clean + rebuild"
    "Host unit tests"
    "Convert GIF (assets/gif -> firmware)"
    "Build Studio exe (Windows)"
    "Quit"
)

PS3=$'\nChoose a number: '
while true; do
    echo
    echo "=== pixel-board ==="
    select opt in "${options[@]}"; do
        case "$REPLY" in
            1) do_build ;;
            2) do_flash ;;
            3) do_monitor ;;
            4) do_all ;;
            5) do_rebuild ;;
            6) do_tests ;;
            7) do_convert_gif ;;
            8) do_build_studio ;;
            9) exit 0 ;;
            *) echo "Invalid choice: $REPLY" ;;
        esac
        break
    done
done
