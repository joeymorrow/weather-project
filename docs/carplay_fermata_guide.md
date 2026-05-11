# 🚗 BEACON In-Car Display Guide (VW Passat 2018)

To display the BEACON Dashboard on the stock radio of your 2018 VW Passat SEL TSI (Fender Audio system) via your wireless dongle, you need an application that allows web views or custom URLs to project over the Android Auto / Apple CarPlay protocol. 

By default, Apple and Google block web browsers on car displays for safety reasons. To bypass this, we use third-party tools like **Fermata Auto**.

## For Android Users (Using Android Auto)

The easiest way to get an unrestricted browser onto your VW display without rooting your phone is by using **AAAD (Android Auto Apps Downloader)**.

### Step 1: Install AAAD
1. On your Android phone, download the latest APK for **AAAD** from its official GitHub repository by **shmykelsa** at [github.com/shmykelsa/AAAD](https://github.com/shmykelsa/AAAD) (do not download from shady app stores).
2. Install the APK. *(Note: You may need to allow your web browser to install apps. Navigate to **Settings > Apps > Special app access > Install unknown apps**, select your browser, and toggle "Allow from this source".)*

### Step 2: Install Fermata Auto
1. Open AAAD. The free version allows you to download one app every 30 days.
2. Select **Fermata Auto** from the list to begin the download.
3. *Note on Trusted Sources:* Because AAAD is downloading another app, Android treats it as an installer. You must set AAAD as a trusted source before installing Fermata. When the security popup appears, tap "Settings", or manually navigate to **Settings > Apps > Special app access > Install unknown apps**, select AAAD, and toggle "Allow from this source".
4. Return to AAAD and confirm the installation of Fermata Auto.
5. Once installed, open Fermata Auto on your phone.

### Step 3: Configure the BEACON Dashboard URL
1. In the Fermata Auto app on your phone, navigate to the **Web Browser** section.
2. Add a new bookmark or set the homepage to your dashboard URL:
   `https://saultweather.morrowedge.com/auto`
   *(Note: Appending `/auto` forces the UI to render the dashboard specifically optimized for landscape in-car displays).*

### Step 4: Launch in the VW Passat
1. Connect your phone to your wireless dongle to boot up Android Auto on the car's screen.
2. Open the Android Auto app drawer.
3. Select **Fermata Auto**.
4. Navigate to the Web Browser tab and select the BEACON bookmark. 

---

## For iPhone Users (Using Apple CarPlay)

Apple's walled garden is much stricter. Getting a web browser onto CarPlay requires either a Jailbreak or utilizing a sideloading exploit like **TrollStore**.

### Method: TrollStore & CarTube
If your iOS version supports TrollStore (generally iOS 14.0 - 15.4.1, though check current compatibility):
1. Install **TrollStore** using your preferred exploit method.
2. Find and download the `.ipa` file for **CarTube** or **CarPlayEnable**.
3. Open the `.ipa` in TrollStore to permanently sign and install it.
4. Open the installed app, enter your URL (`https://saultweather.morrowedge.com/auto`), and it will appear as an available app on your CarPlay dash in the Passat.

### The "Hardware" Alternative (No Jailbreak Required)
If you are using a wireless CarPlay dongle (like a MagicBox, Carlinkit AI Box, or Ottocast), these devices actually run a full version of Android natively *underneath* the CarPlay protocol. 
1. Disconnect your phone temporarily to access the dongle's native Android interface.
2. Open the Google Play Store on the dongle itself.
3. Download **Google Chrome** or **Fully Kiosk Browser**.
4. Set the homepage to your BEACON URL.

*Disclaimer: Always prioritize safety. Do not interact with the dashboard while driving.*