# 🚀 COMPLETE DEPLOYMENT GUIDE - NHL Lineup Generator

## What You'll Have When Done:
A live website at `https://YOUR-APP-NAME.up.railway.app` that anyone can access!

---

## STEP 1: Create GitHub Account (if you don't have one)

1. Go to https://github.com
2. Click "Sign up"
3. Enter email, password, username
4. Verify your email
5. Done! ✅

---

## STEP 2: Upload Code to GitHub

### Option A: Use GitHub Website (Easiest)

1. **Log in to GitHub**: https://github.com

2. **Create New Repository**:
   - Click the `+` in top right corner
   - Click "New repository"
   - Name it: `nhl-lineup-generator`
   - Make it **Public**
   - ✅ Check "Add a README file"
   - Click "Create repository"

3. **Upload Files**:
   - You should see your new empty repository
   - Click "Add file" → "Upload files"
   - Drag ALL these files from the `railway_deploy` folder:
     - `app.py`
     - `Procfile`
     - `requirements.txt`
     - `railway.json`
     - `nixpacks.toml`
   - Click "Commit changes"

4. **Create templates folder**:
   - Click "Add file" → "Create new file"
   - In the name box, type: `templates/index.html`
   - Copy the contents of `index.html` and paste
   - Click "Commit changes"
   - Repeat for `lineup.html`

### Option B: Use GitHub Desktop (Also Easy)

1. Download GitHub Desktop: https://desktop.github.com
2. Install and sign in with your GitHub account
3. Click "Create New Repository"
4. Name: `nhl-lineup-generator`
5. Click "Create Repository"
6. Copy ALL files from `railway_deploy` folder into the repository folder
7. Click "Commit to main"
8. Click "Publish repository"

---

## STEP 3: Deploy to Railway

1. **Go to Railway**: https://railway.app

2. **Sign Up**:
   - Click "Login"
   - Click "Login with GitHub"
   - Authorize Railway to access GitHub
   
3. **Create New Project**:
   - Click "New Project"
   - Click "Deploy from GitHub repo"
   - Select `nhl-lineup-generator`
   - Railway will automatically start deploying!

4. **Wait for Deploy** (2-3 minutes):
   - You'll see logs scrolling
   - Wait for "SUCCESS" message

5. **Get Your URL**:
   - Click "Settings" tab
   - Scroll to "Domains"
   - Click "Generate Domain"
   - You'll get: `https://your-app-name.up.railway.app`
   - **THIS IS YOUR WEBSITE URL!** 🎉

6. **Test It**:
   - Click the URL
   - You should see your NHL Lineup Generator!

---

## STEP 4: Use Your Website

1. Open your Railway URL in any browser
2. Upload forwards screenshot
3. Upload defense screenshot  
4. Click "Generate Lineup"
5. Wait 30-60 seconds
6. New tab opens with lineup!
7. Share the URL with anyone!

---

## 📁 FILES YOU NEED

Download these from Claude and have them ready:

```
railway_deploy/
  ├── app.py
  ├── Procfile
  ├── requirements.txt
  ├── railway.json
  ├── nixpacks.toml
  └── templates/
      ├── index.html
      └── lineup.html
```

---

## 🆘 TROUBLESHOOTING

### "Build Failed" on Railway?
- Check that ALL files are uploaded
- Make sure `templates` folder has both HTML files
- Click "View Logs" to see error

### "Application Error" when visiting site?
- Wait 2 minutes for full deployment
- Check Railway logs for errors
- Make sure Tesseract is in `nixpacks.toml`

### Can't upload files to GitHub?
- Files might be too large
- Try GitHub Desktop instead
- Or use command line (ask for help)

### Need to make changes?
1. Edit files locally
2. Upload to GitHub (replace old files)
3. Railway auto-deploys changes!

---

## 💰 COST

- **Railway Free Tier**: $5 credit/month
- Your app will cost ~$2-3/month
- After free credit: $5/month to keep running
- Can pause/delete anytime

---

## ✅ CHECKLIST

- [ ] GitHub account created
- [ ] Repository created: `nhl-lineup-generator`
- [ ] All 5 files uploaded to root
- [ ] `templates/` folder created
- [ ] Both HTML files in templates folder
- [ ] Railway account created (via GitHub)
- [ ] Project deployed on Railway
- [ ] Domain generated
- [ ] Website is live!

---

## 🎯 FINAL RESULT

Your URL: `https://nhl-lineup-generator-production.up.railway.app`

Anyone with this URL can:
- Upload their lineup screenshots
- Generate printable lineups
- Customize with badges
- Print or save

**YOU'RE DONE!** 🎉

---

## Need Help?

If you get stuck:
1. Check Railway "Logs" tab for errors
2. Make sure all files are in GitHub
3. Try redeploying (click "Deploy" in Railway)
