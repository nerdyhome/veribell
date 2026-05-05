🔔 VeriBell

AI-Powered Video Doorbell Intelligence & Automated Access Control
VeriBell transforms a standard Ring doorbell into an intelligent access control system. 
By analyzing RTSP video streams in real-time using AI, the system identifies known individuals and triggers 
automated actions—ranging from opening gates via Shelly devices to personalized voice announcements via Alexa.  

✨ Key Features

AI Face Recognition: Integrated support for DeepFace and CompreFace for high-accuracy identification.  
Automated Access: Trigger smart home actions (e.g., Shelly relays) based on individual permissions defined in people.json.
Seamless Integration: Connects Ring (via MQTT), Home Assistant (via Webhooks), and Voice Monkey for text-to-speech announcements.  
Web Dashboard: A modern UI to monitor cameras, manage visitors, and visually configure recognition zones (crops).
Privacy First: All video processing and recognition data stay on your local hardware.

🏗️ Architecture

VeriBell is built as a modular microservice environment using Docker:
Mosquitto: Central MQTT broker for event communication.
Ring-MQTT: Bridge between Ring Cloud and local MQTT/RTSP.
go2rtc: High-performance streaming server for video distribution.
VeriBell Core: Python-based recognition engine and web server.  

🚀 Quick Start

1. PrerequisitesDocker and Docker Compose installed.A Ring account and a Ring-MQTT token.(Optional) A CompreFace or DeepFace instance for AI processing.
2. InstallationClone this repository.Copy settings.json.example to settings.json and fill in your credentials.Add your authorized users to people.json.
3. Launch the stack:
   
   docker-compose up -d
   
4. Configuration
Access the web dashboard at http://localhost:5000 to configure your camera crops and monitor events.  

🛡️ Privacy & Security

Important: Never share your ring-state.json, config.json, or passwords files. These contain sensitive tokens and credentials. Always use a .gitignore to keep your private data local.

📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
