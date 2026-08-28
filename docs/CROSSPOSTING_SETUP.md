# Reelay: подключение Facebook, Threads и YouTube

Все дополнительные направления выключены, пока их credentials не добавлены в локальный `.env` и не пройдена отдельная тестовая публикация.

## 1. Facebook Page

Facebook Reels API публикует только в объект **Facebook Page**. Личный профиль
`your personal Facebook profile` из Accounts Center не является Page, даже если на нём
включён Professional mode, поэтому автоматически публиковать туда через Graph
API нельзя. Для Reelay можно либо использовать активную Page `your Facebook Page`, либо
переименовать/создать Page `your personal Facebook profile` и затем заменить Page ID/token.

Что уже есть: Meta App `Reelay`, Page `your Facebook Page`, Page ID, App ID/Secret и текущий Page token. Не хватает права `pages_manage_posts`.

1. Откройте [Meta App Dashboard](https://developers.facebook.com/apps/) и выберите `Reelay`.
2. Откройте **Use cases**. Добавьте или настройте use case управления контентом Facebook Page.
3. В permissions/features добавьте `pages_manage_posts`.
4. Откройте [Graph API Explorer](https://developers.facebook.com/tools/explorer/).
5. Справа выберите Meta App `Reelay` и `User Token`.
6. Добавьте permissions:
   - `pages_show_list`
   - `pages_read_engagement`
   - `pages_manage_posts`
   - `business_management`
7. Нажмите **Generate Access Token** и подтвердите доступ к Page `your Facebook Page`.
8. Выполните запрос:

   ```text
   GET /me/accounts?fields=id,name,access_token,tasks
   ```

9. Убедитесь, что `your Facebook Page` возвращается с задачей `CREATE_CONTENT`.

Что передать Codex: новый **User Access Token** из шага 7. App ID, App Secret и Page ID повторно не нужны. Codex обменяет токен на long-lived и сам получит новый Page Access Token.

Официальные ссылки: [Facebook Reels Publishing](https://developers.facebook.com/docs/video-api/guides/reels-publishing/), [`pages_manage_posts`](https://developers.facebook.com/docs/permissions/reference/pages_manage_posts/), [Meta Postman collection](https://www.postman.com/meta/facebook/folder/simabyk/reels-publishing).

## 2. Threads

1. Убедитесь, что нужный профиль Threads создан и вы можете войти в него.
2. Откройте [Meta App Dashboard](https://developers.facebook.com/apps/).
3. В `Reelay` попробуйте **Add use case → Access the Threads API**. Если Meta не предлагает добавить его в существующее приложение, создайте отдельное приложение `Reelay Threads` с этим use case.
4. Откройте **Threads API → Settings** и скопируйте именно **Threads App ID** и **Threads App Secret**. Они отличаются от обычных Meta App credentials.
5. Откройте **Use cases → Access the Threads API → Customize → Settings**.
6. Внизу, возле **User Token Generator**, нажмите **Add or Remove Threads Testers**.
7. Нажмите **Add People**, выберите роль именно **Threads Tester**, введите username без `@` (`rbc_haze_harris`) и отправьте приглашение.
8. Войдите в нужный Threads-аккаунт и откройте [Website permissions](https://www.threads.com/settings/website_permissions). На вкладке приглашений выберите приложение Reelay и нажмите **Accept**.
9. Вернитесь в **Use cases → Access the Threads API → Customize → Settings**, обновите страницу и в **User Token Generator** нажмите **Generate Access Token** напротив `rbc_haze_harris`.
10. Разрешите:
   - `threads_basic`
   - `threads_content_publish`

Что передать Codex:

```text
Threads username:
Threads App ID:
Threads App Secret:
Threads User Access Token:
```

Codex определит Threads User ID, обменяет токен на long-lived и сохранит его локально.

Threads API не поддерживает загрузку локального файла: он принимает только
публичный `video_url`. Instagram CDN оказался ненадёжным источником для Threads
video processing. Поэтому Reelay перед каждой Threads-публикацией:

1. создаёт временный MP4 H.264 + AAC-LC без edit lists;
2. поднимает локальный сервер, отдающий только этот `video.mp4`;
3. открывает одноразовый Cloudflare Quick Tunnel;
4. ждёт `FINISHED`, публикует контейнер и сразу закрывает туннель;
5. удаляет временный файл независимо от результата.

Исполняемый `cloudflared` должен находиться в `data/bin/cloudflared` или в
`PATH`. Постоянный сервер не нужен, но во время обработки один MP4 временно
проходит через инфраструктуру Cloudflare и доступен по случайному HTTPS URL.

Официальные ссылки: [Threads Get Started](https://developers.facebook.com/docs/threads/get-started), [Tokens and permissions](https://developers.facebook.com/docs/threads/get-started/get-access-tokens-and-permissions), [Publishing](https://developers.facebook.com/docs/threads/posts), [официальный sample](https://github.com/fbsamples/threads_api).

## 3. YouTube Shorts

1. Создайте или выберите проект в [Google Cloud Console](https://console.cloud.google.com/projectcreate).
2. Откройте [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com) и нажмите **Enable**.
3. Откройте **Google Auth Platform**:
   - **Branding**: имя `Reelay`, ваш support email;
   - **Audience**: `External`;
   - **Test users**: добавьте Google email, которому принадлежит YouTube-канал;
   - **Data Access**: добавьте scopes
     `https://www.googleapis.com/auth/youtube.upload` и
     `https://www.googleapis.com/auth/youtube.readonly`. Второй нужен только,
     чтобы перед сохранением токена проверить название и ID выбранного канала.
4. Откройте **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
5. Выберите application type **Desktop app**, назовите `Reelay Local`.
6. Скачайте JSON через **Download JSON** и скопируйте значения `client_id` и `client_secret` из секции `installed` в локальный `.env`:

   ```dotenv
   YOUTUBE_CLIENT_ID=...
   YOUTUBE_CLIENT_SECRET=...
   PUBLISH_YOUTUBE=false
   ```

7. Из папки проекта запустите локальный OAuth bootstrap:

   ```bash
   uv run python -m reelay.youtube_oauth
   ```

8. В системном браузере выберите нужный Google/YouTube account и нажмите **Allow**. Команда покажет имя и ID выбранного канала и попросит подтверждение. После подтверждения она атомарно сохранит `YOUTUBE_REFRESH_TOKEN` и `YOUTUBE_CHANNEL_ID` в локальный `.env`, не печатая секреты.
9. Для строгой автоматической проверки заранее известного канала можно выполнить:

   ```bash
   uv run python -m reelay.youtube_oauth --expected-channel-id UCxxxxxxxx
   ```

10. После успешного bootstrap перезапустите Reelay и выполните в Telegram
    `/youtube ID` для одного изолированного private-теста. Команда не публикует
    ролик повторно в Instagram и не удаляет его из очереди.
11. После успешного теста установите `PUBLISH_YOUTUBE=true` и снова перезапустите
    Reelay. С этого момента общая очередь будет сохранять YouTube video ID и не
    дублировать уже успешную загрузку при `/retry`.

Первый тест будет `private`. Проекты YouTube API, не прошедшие compliance audit, принудительно оставляют API-загрузки приватными. Для публичных Shorts затем заполните [YouTube API Audit and Quota Extension Form](https://support.google.com/youtube/contact/yt_api_form). В форме укажите, что приложение локально загружает только авторизованный пользователем контент в его собственный канал через `videos.insert`.

Официальные ссылки: [Upload a video](https://developers.google.com/youtube/v3/guides/uploading_a_video), [`videos.insert`](https://developers.google.com/youtube/v3/docs/videos/insert), [OAuth for installed apps](https://developers.google.com/youtube/v3/guides/auth/installed-apps), [3-minute Shorts](https://support.google.com/youtube/answer/15424877), [API audit](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits).

## Ответ одним сообщением

Когда шаги выполнены, пришлите:

```text
Facebook User Access Token:

Threads username:
Threads App ID:
Threads App Secret:
Threads User Access Token:

YouTube OAuth: выполнен / не выполнен
YouTube channel ID:
```
