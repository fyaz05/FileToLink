<h1 align="center">🚀 File To Link Bot 🌍</h1>

<p align="center">
  <a href="#">
    <img src="https://cdn.jsdelivr.net/gh/fyaz05/Resources@main/HydroStreamerBot/Thunder.jpg" height="100" width="100" alt="Telegram Logo">
  </a>
</p>

<p align="center">
  <b>Telegram Advanced File to Link Bot</b><br/>
  Convert files to links for seamless streaming and downloading with advanced features using Hydrogram.
</p>

<p align="center">
  <a href="https://github.com/fyaz05/FileToLink/issues">🐞 Report a Bug</a>
  |
  <a href="https://github.com/fyaz05/FileToLink/issues">🌟 Request a Feature</a>
</p>

<hr>

<details open="open">
  <summary>📋 Table of Contents</summary>
  <ol>
    <li>
      <a href="#about-this-bot">About This Bot</a>
      <ul>
        <li><a href="#features">Features</a></li>
      </ul>
    </li>
    <li>
      <a href="#how-to-make-your-own">How to Make Your Own</a>
      <ul>
        <li><a href="#deploy-on-heroku">Deploy on Heroku</a></li>
        <li><a href="#host-it-on-vps-or-locally">Run It on a VPS / Locally</a></li>
      </ul>
    </li>
    <li>
      <a href="#setting-up-things">Setting Up Things</a>
      <ul>
        <li><a href="#mandatory-vars">Mandatory Vars</a></li>
        <li><a href="#optional-vars">Optional Vars</a></li>
      </ul>
    </li>
    <li><a href="#how-to-use-the-bot">How to Use the Bot</a></li>
    <li><a href="#faq">FAQ</a></li>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#credits">Credits</a></li>
  </ol>
</details>

## 🤖 About This Bot

### ⚙️ Features

- **Streamlined Code**: Improved efficiency by removing unnecessary features.
- **High Speed**: Utilizes Hydrogram for faster operations.
- **Easy Deployment**: Configured for straightforward deployment.
- **Enhanced Functionality**: Improved user interface and added features:
  - 😄 **User-Friendly Interface**
  - 🔗 **Instant Stream Links**
  - 👥 **Group Support**
  - 📂 **File Retrieval**
  - 📢 **Channel Updates**
  - 📑 **Log Channel**
  - 🚨 **Admin Broadcasts**

### 💻 Bot Commands

<details>
  <summary><strong>View All Commands</strong> <sup><kbd>(Click to expand)</kbd></sup></summary>

```
start - Start the bot
link - Generate a stream link
help - Bot usage details
about - Get bot info
dc - Check data center
ping - Check bot latency
stats - (Admin) Bot usage statistics
status - (Admin) Bot operational status
broadcast - (Admin) Send a message to all users
users - (Admin) View total users
```

</details>

## 🚀 How to Make Your Own

### Deploy on Heroku

Press the button below to deploy on Heroku:

[![Deploy To Heroku](https://www.herokucdn.com/deploy/button.svg)](https://dashboard.heroku.com/new-app?template=https://github.com/fyaz05/FileToLink)

Then, refer to the [variables tab](#mandatory-vars) for more info on setting up environmental variables.

### Host It on VPS or Locally

```sh
git clone https://github.com/fyaz05/FileToLink
cd FileToLink
pip3 install -r requirements.txt
python3 -m Thunder
```

To stop the bot:

```sh
Ctrl + C
```

If you want to run the bot 24/7 on VPS:

```sh
sudo apt install tmux -y
tmux
python3 -m Thunder
```

Now you can close the VPS, and the bot will keep running.

## ⚙️ Setting Up Things

If you're on Heroku, just add these to the Environmental Variables. If you're hosting locally, create a `.env` file in the root directory and add all the variables there. Example `.env` file:

```sh
API_ID=
API_HASH=
BOT_TOKEN=
BIN_CHANNEL=
DATABASE_URL=
FQDN=
HAS_SSL=
OWNER_ID=
OWNER_USERNAME=
PORT=
#Remove hash for using multiple tokens and max token up to 49
#MULTI_TOKEN1=
#MULTI_TOKEN2=
#MULTI_TOKEN3=
```

### 🔐 Mandatory Vars

- **`API_ID`**: Get it from [my.telegram.org](https://my.telegram.org).
- **`API_HASH`**: Get it from [my.telegram.org](https://my.telegram.org).
- **`BOT_TOKEN`**: Get the bot token from [@BotFather](https://telegram.dog/BotFather).
- **`BIN_CHANNEL`**: Create a new channel (private/public), post something in your channel, forward that post to [@missrose_bot](https://telegram.dog/MissRose_bot), and reply with `/id`. Copy the forwarded channel ID here.
- **`OWNER_ID`**: Your Telegram User ID. Send `/id` to [@missrose_bot](https://telegram.dog/MissRose_bot) to get it.
- **`DATABASE_URL`**: MongoDB URI for saving user IDs for broadcasting.

### 🔧 Optional Vars

`UPDATES_CHANNEL`: Public channel username that users must join to use the bot. Ensure bot is an admin there.

`BANNED_CHANNELS`: IDs of channels where the bot won't work. Separate multiple IDs with a <kbd>Space</kbd>.

`SLEEP_THRESHOLD`: Time (in seconds) for bot to handle flood wait exceptions automatically. Defaults to 60 seconds.

`WORKERS`: Max number of concurrent workers for updates. Defaults to `3`.

`PORT`: The port for your web app's deployment. Defaults to `8080`.

`MY_PASS`: Bot PASSWORD.

`WEB_SERVER_BIND_ADDRESS`: Your server's bind address. Defaults to `0.0.0.0`.

`NO_PORT`: Set your `PORT` to `80` (http) or `443` (https) if you want the port hidden. Ignore if using Heroku.

`FQDN`: A Fully Qualified Domain Name, if present. Defaults to `WEB_SERVER_BIND_ADDRESS`.

## 📟 How to Use the Bot

⚠️ **Before using the bot, add all relevant bots (multi-client ones too) to the `BIN_CHANNEL` as admins.**

- **`/start`**: Check if the bot is active.
- To get an instant stream link, forward any media to the bot.
- To use in a group, add the bot as admin and reply to a file with /link.

## ❓ FAQ

- **How long do the links remain valid?**

  Links remain valid as long as your bot is running and you haven't deleted the log channel.

## 🤝 Contributing

Feel free to contribute to this project if you have any ideas or improvements in mind!

## 🏅 Credits

- [Me](https://github.com/fyaz05)
- Adarsh Goel
