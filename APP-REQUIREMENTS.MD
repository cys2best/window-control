Update PC server to support React Native iOS app:

1. Add CORS middleware to FastAPI:
   allow all origins, methods, headers

2. Update WebSocket input handler:
   - Accept BINARY messages (not JSON)
   - Parse 10-byte binary format:
     struct.unpack('>BBHHBHx', data)
     fields: type, finger_id, x, y, 
             action, pressure, reserved
   - Support multi-touch:
     route each finger_id independently
     to py-scrcpy-client control socket
   - Input types:
     0x01 = TOUCH_DOWN
     0x02 = TOUCH_MOVE
     0x03 = TOUCH_UP
     0x04 = KEY_EVENT
     0x05 = SWIPE

3. Add resolution endpoint:
   GET /instances/{id}/resolution
   Returns: {"width": int, "height": int}
   Get from ADB: 
   adb shell wm size

4. Update mediamtx.yml config:
   Add: httpCORSOrigin: "*"

5. Keep everything else unchanged:
   - scrcpy pipeline
   - mediamtx streaming
   - instance management
   - Tailscale detection
   - PyQt5 launcher

Build a React Native iOS app that connects to a PC streaming server
over Tailscale, displays LDPlayer instance streams via WebRTC/WHEP,
and controls them with ultra-low latency touch input.

## App Overview:
- Stream viewer for LDPlayer Android emulator instances
- Connect to PC server via Tailscale IP
- Ultra-low latency touch control for gaming
- Multi-instance switching
- No App Store needed — distribute via TestFlight

## Tech Stack:
- React Native 0.73+
- TypeScript
- react-native-webrtc (WebRTC/WHEP streaming)
- react-native-gesture-handler (low latency touch)
- react-native-reanimated (smooth UI animations)
- zustand (state management)
- react-native-haptic-feedback (vibration on touch)
- react-native-keep-awake (prevent screen sleep)
- react-native-orientation-locker (force landscape)

## Project Structure:
/src
  - App.tsx                    # Root component
  - screens/
    - ConnectScreen.tsx        # Server connection UI
    - StreamScreen.tsx         # Main streaming + control
    - InstanceListScreen.tsx   # Switch between instances
    - SettingsScreen.tsx       # App settings
  - components/
    - StreamView.tsx           # WebRTC video renderer
    - TouchLayer.tsx           # Ultra-low latency touch
    - GamepadOverlay.tsx       # Virtual gamepad buttons
    - InstanceSwitcher.tsx     # Bottom instance panel
    - ConnectionStatus.tsx     # Network status indicator
    - LatencyDisplay.tsx       # Show current latency ms
  - hooks/
    - useWebRTC.ts             # WebRTC/WHEP connection
    - useWebSocket.ts          # Input WebSocket connection
    - useTouch.ts              # Touch event processing
    - useInstances.ts          # Instance management
    - useTailscale.ts          # Server discovery
  - services/
    - WebRTCService.ts         # WHEP client implementation
    - InputService.ts          # Binary input protocol
    - InstanceService.ts       # Instance API calls
  - store/
    - connectionStore.ts       # Zustand connection state
    - instanceStore.ts         # Zustand instance state
    - settingsStore.ts         # Zustand settings state
  - utils/
    - coordinates.ts           # Touch coordinate translation
    - binary.ts                # Binary message encoding
    - network.ts               # Network utilities
  - types/
    - index.ts                 # TypeScript interfaces

## Screen 1: ConnectScreen
- Clean minimal UI
- Input field for Tailscale IP address
- Port field (default 8889)
- Save/remember last connection
- Connect button
- Show connection error clearly
- Recent connections list
- Auto-connect on app launch if saved

## Screen 2: StreamScreen (Main Screen)
- Full screen video stream (no UI chrome)
- Force landscape orientation
- Keep screen always on (react-native-keep-awake)
- True fullscreen (hide status bar)
- TouchLayer overlaid on video:
  * Captures ALL touch events
  * Sends immediately on touchstart (not touchend)
  * No 300ms delay
  * Multi-touch up to 10 fingers
  * Prevents default iOS gestures conflicting
- GamepadOverlay (optional, togglable):
  * Semi-transparent buttons
  * Configurable positions
  * D-pad zone
  * Action buttons (A/B/X/Y)
  * Back/Home buttons
- Swipe up from bottom → show InstanceSwitcher
- Swipe down → show settings panel
- Double tap → toggle gamepad overlay
- LatencyDisplay in corner (small, unobtrusive)
- ConnectionStatus indicator

## Screen 3: InstanceSwitcher Panel
- Slides up from bottom (like iOS app switcher)
- Shows all available LDPlayer instances:
  * Instance thumbnail (refreshes every 2s)
  * Instance name/number
  * Currently active highlighted
- Tap instance → switch stream instantly
- Swipe down to dismiss

## Touch Input System (Critical for gaming):
- Use react-native-gesture-handler NOT
  React Native's built-in touch system
- Gesture handler runs on UI thread
  (not JS thread) = much lower latency
- TouchLayer component:
  * position: absolute, full screen
  * zIndex: highest
  * pointerEvents: box-only
  * Capture raw touch events
  * NO gesture recognizer conflicts

- Binary WebSocket protocol:
  Each touch event = 10 bytes:
  [type(1)][finger_id(1)][x(2)][y(2)][action(1)][pressure(2)][reserved(1)]

  Types:
  0x01 = TOUCH_DOWN
  0x02 = TOUCH_MOVE  
  0x03 = TOUCH_UP
  0x04 = KEY_EVENT
  0x05 = SWIPE

  Actions for KEY_EVENT:
  0x01 = BACK
  0x02 = HOME
  0x03 = MENU
  0x04 = POWER

- Send touch events immediately:
  onBegan → TOUCH_DOWN
  onActive → TOUCH_MOVE (throttle to 60fps)
  onEnded → TOUCH_UP
  onCancelled → TOUCH_UP

- Coordinate translation:
  iPhone screen px → LDPlayer resolution px
  Account for video aspect ratio
  Account for letterbox/pillarbox bars

## WebRTC/WHEP Implementation:
- Connect to mediamtx WHEP endpoint:
  GET http://{tailscale-ip}:{port}/{instance_id}/whep
- Use react-native-webrtc RTCPeerConnection
- WHEP flow:
  1. Create RTCPeerConnection
  2. Create SDP offer
  3. POST offer to WHEP endpoint
  4. Get SDP answer back
  5. Set remote description
  6. ICE candidates via Link headers
  7. Video stream starts playing
- Handle reconnection automatically:
  * Detect stream drop
  * Wait 1 second
  * Reconnect automatically
  * Show reconnecting indicator

## Input WebSocket:
- Connect to ws://{tailscale-ip}:8765
- Send binary messages (ArrayBuffer)
- Include instance_id in every message
- Ping every 5 seconds to keep alive
- Reconnect on drop automatically
- Queue messages if temporarily disconnected

## Settings Screen:
- Server IP and port
- Stream quality (Low/Medium/High)
- Max FPS (15/30/60)
- Show/hide latency display
- Gamepad overlay opacity
- Haptic feedback on/off
- Auto-connect on launch
- Reset to defaults

## Performance Requirements:
- Touch to input delivery: < 30ms
- Video latency: < 100ms  
- UI thread never blocked
- All touch processing on UI thread
- JS thread only for business logic
- 60fps UI rendering minimum

## iOS Specific Optimizations:
- Disable iOS system gestures interfering:
  * Edge swipe navigation
  * Pull to refresh
  * Bounce scroll
- Set UIScrollView bounces = false
- Set preferredScreenEdgesDeferringSystemGestures
- Request always-on display
- Request landscape lock
- Disable iOS tap delay completely:
  UIGestureRecognizer cancelsTouchesInView = false

## Important iOS Settings (Info.plist):
- UIRequiresFullScreen = true
- UISupportedInterfaceOrientations = Landscape only
- NSCameraUsageDescription (for future QR scan)
- NSLocalNetworkUsageDescription

## Build & Distribution:
- Target iOS 15+
- Xcode project configured
- TestFlight ready (no App Store needed)
- Bundle ID: com.yourname.ldcontrol
- App name: "LD Control"

## Package.json dependencies:
- react-native: 0.73+
- react-native-webrtc
- react-native-gesture-handler
- react-native-reanimated
- react-native-screens
- react-native-safe-area-context
- react-native-keep-awake
- react-native-orientation-locker
- react-native-haptic-feedback
- zustand
- @react-navigation/native
- @react-navigation/stack
- axios (REST API calls)

## README must include:
- Mac development setup
- iOS simulator testing
- TestFlight distribution steps
- How to connect to PC server
- Troubleshooting common issues