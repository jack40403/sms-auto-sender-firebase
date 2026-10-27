import { createHmac, createHash, randomBytes, randomInt, timingSafeEqual } from 'node:crypto'
import { initializeApp } from 'firebase-admin/app'
import { FieldValue, Timestamp, getFirestore } from 'firebase-admin/firestore'
import { getStorage } from 'firebase-admin/storage'
import { defineSecret, defineString } from 'firebase-functions/params'
import { setGlobalOptions } from 'firebase-functions/v2'
import { onRequest } from 'firebase-functions/v2/https'
import type { Request, Response } from 'express'
import nodemailer from 'nodemailer'

initializeApp()

setGlobalOptions({
  region: 'us-central1',
  invoker: 'public',
  memory: '256MiB',
  timeoutSeconds: 30,
  minInstances: 0,
  maxInstances: 3,
})

const ownerApprovalEmail = defineString('OWNER_APPROVAL_EMAIL')
const CODE_TTL_MS = 10 * 60 * 1000
const RESEND_COOLDOWN_MS = 60 * 1000
const MAX_CODE_ATTEMPTS = 5
const MAX_REQUESTS_PER_IP_PER_HOUR = 10
const MAX_PASSWORD_ATTEMPTS_PER_IP_PER_HOUR = 10
const UPDATE_URL_TTL_MS = 15 * 60 * 1000
const INSTALLER_PREFIX = 'SMSAutoSender-Setup-'

const smtpAppPassword = defineSecret('SMTP_APP_PASSWORD')
const activationHmacSecret = defineSecret('ACTIVATION_HMAC_SECRET')
const activationBranchPasswords = defineSecret('ACTIVATION_BRANCH_PASSWORDS')

const db = getFirestore()

class ApiError extends Error {
  constructor(readonly statusCode: number, message: string) {
    super(message)
  }
}

function setCommonHeaders(response: Response) {
  response.set('Access-Control-Allow-Origin', '*')
  response.set('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Installation-Id, X-App-Version')
  response.set('Access-Control-Allow-Methods', 'GET, HEAD, POST, OPTIONS')
  response.set('X-Content-Type-Options', 'nosniff')
  response.set('Cache-Control', 'no-store')
}

function handlePreflight(request: Request, response: Response) {
  setCommonHeaders(response)
  if (request.method === 'OPTIONS') {
    response.status(204).send('')
    return true
  }
  return false
}

function sendError(response: Response, error: unknown) {
  if (error instanceof ApiError) {
    response.status(error.statusCode).json({ success: false, message: error.message })
    return
  }
  console.error(error)
  response.status(500).json({ success: false, message: '伺服器暫時發生錯誤，請稍後再試。' })
}

function parseBody(request: Request) {
  if (!request.body || typeof request.body !== 'object') {
    throw new ApiError(400, '請求內容格式不正確。')
  }
  return request.body as Record<string, unknown>
}

function validateInstallationId(value: unknown) {
  const installationId = typeof value === 'string' ? value.trim() : ''
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(installationId)) {
    throw new ApiError(400, '安裝編號格式不正確。')
  }
  return installationId
}

function validateStoreName(value: unknown) {
  const storeName = typeof value === 'string' ? value.trim() : ''
  if (storeName.length < 2 || storeName.length > 80) {
    throw new ApiError(400, '請輸入 2 到 80 個字的藥局名稱。')
  }
  return storeName
}

function validateAppVersion(value: unknown) {
  const appVersion = typeof value === 'string' ? value.trim() : ''
  return /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(appVersion) ? appVersion : 'unknown'
}

function validateActivationPassword(value: unknown) {
  const password = typeof value === 'string' ? value : ''
  if (password.length < 8 || password.length > 128) {
    throw new ApiError(400, '管理密碼長度必須介於 8 到 128 個字元。')
  }
  return password
}

function hmac(value: string) {
  return createHmac('sha256', activationHmacSecret.value()).update(value).digest('hex')
}

function safeHashEquals(left: string, right: string) {
  const leftBuffer = Buffer.from(left, 'hex')
  const rightBuffer = Buffer.from(right, 'hex')
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer)
}

function codeHash(installationId: string, code: string) {
  return hmac(`activation-code:${installationId}:${code}`)
}

function normalizeBranchId(storeName: string) {
  return storeName.trim().normalize('NFKC').toLocaleLowerCase('zh-TW')
}

function branchBindingId(branchId: string) {
  return createHash('sha256').update(`activation-branch:${branchId}`).digest('hex')
}

function activationPasswordHash(branchId: string, password: string) {
  return hmac(`activation-password:${branchId}:${password}`)
}

function readBranchPasswords() {
  const raw = activationBranchPasswords.value().trim()
  if (!raw) throw new ApiError(503, '分店管理密碼尚未設定，請聯絡管理者。')

  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    throw new ApiError(503, '分店管理密碼設定格式錯誤，請聯絡管理者。')
  }

  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new ApiError(503, '分店管理密碼設定格式錯誤，請聯絡管理者。')
  }

  return parsed as Record<string, unknown>
}

function licenseHash(installationId: string, token: string) {
  return hmac(`license-token:${installationId}:${token}`)
}

function requestIpHash(request: Request) {
  const forwarded = request.header('x-forwarded-for')?.split(',')[0]?.trim()
  const ip = forwarded || request.ip || 'unknown'
  return createHash('sha256').update(ip).digest('hex')
}

function escapeHtml(value: string) {
  return value.replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    "'": '&#39;',
    '"': '&quot;',
  })[character] || character)
}

export const requestActivation = onRequest(
  { secrets: [smtpAppPassword, activationHmacSecret] },
  async (request, response) => {
    if (handlePreflight(request, response)) return
    if (request.method !== 'POST') {
      response.status(405).json({ success: false, message: '只接受 POST 請求。' })
      return
    }

    try {
      const body = parseBody(request)
      const installationId = validateInstallationId(body.installationId)
      const storeName = validateStoreName(body.storeName)
      const appVersion = validateAppVersion(body.appVersion)
      const code = String(randomInt(100000, 1000000))
      const now = Timestamp.now()
      const expiresAt = Timestamp.fromMillis(now.toMillis() + CODE_TTL_MS)
      const requestRef = db.collection('activationRequests').doc(installationId)
      const ipRef = db.collection('activationRateLimits').doc(requestIpHash(request))

      await db.runTransaction(async (transaction) => {
        const [requestSnapshot, ipSnapshot] = await Promise.all([
          transaction.get(requestRef),
          transaction.get(ipRef),
        ])

        const previousRequestedAt = requestSnapshot.get('requestedAt') as Timestamp | undefined
        if (previousRequestedAt && now.toMillis() - previousRequestedAt.toMillis() < RESEND_COOLDOWN_MS) {
          throw new ApiError(429, '驗證碼申請太頻繁，請一分鐘後再試。')
        }

        const windowStartedAt = ipSnapshot.get('windowStartedAt') as Timestamp | undefined
        const existingCount = Number(ipSnapshot.get('requestCount') || 0)
        const sameWindow = windowStartedAt && now.toMillis() - windowStartedAt.toMillis() < 60 * 60 * 1000
        const requestCount = sameWindow ? existingCount + 1 : 1
        if (requestCount > MAX_REQUESTS_PER_IP_PER_HOUR) {
          throw new ApiError(429, '這個網路位置申請次數過多，請稍後再試。')
        }

        transaction.set(ipRef, {
          windowStartedAt: sameWindow ? windowStartedAt : now,
          requestCount,
          updatedAt: now,
        })
        transaction.set(requestRef, {
          installationId,
          storeName,
          appVersion,
          codeHash: codeHash(installationId, code),
          requestedAt: now,
          expiresAt,
          attempts: 0,
          status: 'pending',
          mailStatus: 'sending',
        })
      })

      const isEmulator = process.env.FUNCTIONS_EMULATOR === 'true'
      if (isEmulator) {
        console.info(`[Emulator] Activation code for ${installationId}: ${code}`)
        await requestRef.update({ mailStatus: 'emulated', mailSentAt: FieldValue.serverTimestamp() })
      } else {
        const transporter = nodemailer.createTransport({
          service: 'gmail',
          auth: {
            user: ownerApprovalEmail.value(),
            pass: smtpAppPassword.value().replace(/\s/g, ''),
          },
        })

        try {
          await transporter.sendMail({
            from: `簡訊自動發送系統 <${ownerApprovalEmail.value()}>`,
            to: ownerApprovalEmail.value(),
            subject: `安裝驗證碼：${storeName}`,
            text: [
              '簡訊自動發送系統安裝申請',
              '',
              `藥局名稱：${storeName}`,
              `軟體版本：${appVersion}`,
              `安裝編號：${installationId}`,
              `驗證碼：${code}`,
              '',
              '驗證碼十分鐘內有效，只能使用一次。',
            ].join('\n'),
            html: `<h2>簡訊自動發送系統安裝申請</h2>
            <p>藥局名稱：<strong>${escapeHtml(storeName)}</strong></p>
            <p>軟體版本：${escapeHtml(appVersion)}</p>
            <p>安裝編號：<code>${installationId}</code></p>
            <p style="font-size:30px;font-weight:700;letter-spacing:6px">${code}</p>
            <p>驗證碼十分鐘內有效，只能使用一次。</p>`,
          })
          await requestRef.update({ mailStatus: 'sent', mailSentAt: FieldValue.serverTimestamp() })
        } catch (mailError) {
          console.error('Activation email failed:', mailError)
          await requestRef.update({ mailStatus: 'failed' })
          throw new ApiError(502, '驗證郵件暫時無法寄出，請檢查寄信設定後再試。')
        }
      }

      response.status(200).json({
        success: true,
        message: '驗證碼已寄到管理者信箱。',
        expiresAt: expiresAt.toDate().toISOString(),
        ...(isEmulator ? { developmentCode: code } : {}),
      })
    } catch (error) {
      sendError(response, error)
    }
  },
)

export const verifyActivation = onRequest(
  { secrets: [activationHmacSecret] },
  async (request, response) => {
    if (handlePreflight(request, response)) return
    if (request.method !== 'POST') {
      response.status(405).json({ success: false, message: '只接受 POST 請求。' })
      return
    }

    try {
      const body = parseBody(request)
      const installationId = validateInstallationId(body.installationId)
      const code = typeof body.code === 'string' ? body.code.replace(/\D/g, '') : ''
      const appVersion = validateAppVersion(body.appVersion)
      if (!/^\d{6}$/.test(code)) throw new ApiError(400, '請輸入六位數驗證碼。')

      const requestRef = db.collection('activationRequests').doc(installationId)
      const installationRef = db.collection('installations').doc(installationId)
      const licenseToken = randomBytes(32).toString('base64url')
      const now = Timestamp.now()

      const verificationResult = await db.runTransaction(async (transaction) => {
        const snapshot = await transaction.get(requestRef)
        if (!snapshot.exists || snapshot.get('status') !== 'pending') {
          return { error: new ApiError(404, '找不到有效的安裝申請，請重新申請驗證碼。') }
        }

        const expiresAt = snapshot.get('expiresAt') as Timestamp
        const attempts = Number(snapshot.get('attempts') || 0)
        if (expiresAt.toMillis() < now.toMillis()) {
          transaction.update(requestRef, { status: 'expired' })
          return { error: new ApiError(410, '驗證碼已過期，請重新申請。') }
        }
        if (attempts >= MAX_CODE_ATTEMPTS) {
          transaction.update(requestRef, { status: 'locked' })
          return { error: new ApiError(429, '驗證錯誤次數過多，請重新申請驗證碼。') }
        }

        const storedCodeHash = String(snapshot.get('codeHash') || '')
        if (!safeHashEquals(storedCodeHash, codeHash(installationId, code))) {
          transaction.update(requestRef, { attempts: attempts + 1 })
          return { error: new ApiError(401, `驗證碼不正確，還可嘗試 ${Math.max(0, MAX_CODE_ATTEMPTS - attempts - 1)} 次。`) }
        }

        const storeName = String(snapshot.get('storeName') || '')
        transaction.set(installationRef, {
          installationId,
          storeName,
          active: true,
          licenseTokenHash: licenseHash(installationId, licenseToken),
          activatedAt: now,
          lastActivatedVersion: appVersion,
        })
        transaction.update(requestRef, {
          status: 'approved',
          approvedAt: now,
          codeHash: FieldValue.delete(),
        })
        return { error: null }
      })

      if (verificationResult.error) throw verificationResult.error

      response.status(200).json({
        success: true,
        message: '安裝授權完成。',
        licenseToken,
      })
    } catch (error) {
      sendError(response, error)
    }
  },
)

export const verifyActivationPassword = onRequest(
  { secrets: [activationHmacSecret, activationBranchPasswords] },
  async (request, response) => {
    if (handlePreflight(request, response)) return
    if (request.method !== 'POST') {
      response.status(405).json({ success: false, message: '只接受 POST 請求。' })
      return
    }

    try {
      const body = parseBody(request)
      const installationId = validateInstallationId(body.installationId)
      const storeName = validateStoreName(body.storeName)
      const branchId = normalizeBranchId(storeName)
      const appVersion = validateAppVersion(body.appVersion)
      const password = validateActivationPassword(body.password)
      const branchPasswords = readBranchPasswords()
      const configuredPassword = branchPasswords[branchId]
      if (typeof configuredPassword !== 'string') {
        throw new ApiError(404, '這家分店尚未設定管理密碼，請聯絡管理者。')
      }
      if (configuredPassword.length < 8 || configuredPassword.length > 128) {
        throw new ApiError(503, '這家分店的管理密碼設定格式錯誤，請聯絡管理者。')
      }

      const attemptRef = db.collection('activationPasswordRateLimits').doc(requestIpHash(request))
      const now = Timestamp.now()
      await db.runTransaction(async (transaction) => {
        const snapshot = await transaction.get(attemptRef)
        const windowStartedAt = snapshot.get('windowStartedAt') as Timestamp | undefined
        const existingCount = Number(snapshot.get('attemptCount') || 0)
        const sameWindow = windowStartedAt && now.toMillis() - windowStartedAt.toMillis() < 60 * 60 * 1000
        const attemptCount = sameWindow ? existingCount + 1 : 1
        if (attemptCount > MAX_PASSWORD_ATTEMPTS_PER_IP_PER_HOUR) {
          throw new ApiError(429, '管理密碼嘗試次數過多，請稍後再試。')
        }
        transaction.set(attemptRef, {
          windowStartedAt: sameWindow ? windowStartedAt : now,
          attemptCount,
          updatedAt: now,
        })
      })

      if (!safeHashEquals(activationPasswordHash(branchId, password), activationPasswordHash(branchId, configuredPassword))) {
        throw new ApiError(401, '管理密碼不正確。')
      }

      const bindingRef = db.collection('activationPasswordBindings').doc(branchBindingId(branchId))
      const installationRef = db.collection('installations').doc(installationId)
      const licenseToken = randomBytes(32).toString('base64url')
      const activatedAt = Timestamp.now()
      const bindingResult = await db.runTransaction(async (transaction) => {
        const bindingSnapshot = await transaction.get(bindingRef)
        const boundInstallationId = String(bindingSnapshot.get('installationId') || '')
        const bindingActive = bindingSnapshot.get('active') !== false

        if (boundInstallationId && boundInstallationId !== installationId) {
          return { error: new ApiError(409, '這家分店的管理密碼已綁定另一台裝置。') }
        }
        if (boundInstallationId === installationId && !bindingActive) {
          return { error: new ApiError(403, '這家分店的管理密碼目前已停用，請聯絡管理者。') }
        }

        transaction.set(bindingRef, {
          branchId,
          installationId,
          storeName,
          active: true,
          boundAt: bindingSnapshot.get('boundAt') || activatedAt,
          lastUsedAt: activatedAt,
        }, { merge: true })
        transaction.set(installationRef, {
          installationId,
          branchId,
          storeName,
          active: true,
          licenseTokenHash: licenseHash(installationId, licenseToken),
          activatedAt,
          lastActivatedVersion: appVersion,
          activationMethod: 'password',
        }, { merge: true })
        return { error: null }
      })

      if (bindingResult.error) throw bindingResult.error

      response.status(200).json({
        success: true,
        message: '分店管理密碼驗證成功，安裝授權完成。',
        licenseToken,
      })
    } catch (error) {
      sendError(response, error)
    }
  },
)

async function requireActiveLicense(request: Request) {
  const installationId = validateInstallationId(request.header('x-installation-id'))
  const authorization = request.header('authorization') || ''
  const token = authorization.startsWith('Bearer ') ? authorization.slice(7).trim() : ''
  if (!token) throw new ApiError(401, '缺少更新授權。')

  const snapshot = await db.collection('installations').doc(installationId).get()
  if (!snapshot.exists || snapshot.get('active') !== true) {
    throw new ApiError(403, '這個安裝尚未啟用，或更新權限已停用。')
  }

  const storedHash = String(snapshot.get('licenseTokenHash') || '')
  if (!safeHashEquals(storedHash, licenseHash(installationId, token))) {
    throw new ApiError(403, '更新授權無效。')
  }
  return installationId
}

export const validateActivation = onRequest(
  { secrets: [activationHmacSecret] },
  async (request, response) => {
    if (handlePreflight(request, response)) return
    if (request.method !== 'POST') {
      response.status(405).json({ success: false, message: '只接受 POST 請求。' })
      return
    }

    try {
      const body = parseBody(request)
      const installationId = await requireActiveLicense(request)
      const installationRef = db.collection('installations').doc(installationId)
      const snapshot = await installationRef.get()
      const appVersion = validateAppVersion(body.appVersion)
      const now = FieldValue.serverTimestamp()
      const storeName = String(snapshot.get('storeName') || '')

      await installationRef.set({
        lastValidatedAt: now,
        lastValidatedVersion: appVersion,
      }, { merge: true })

      response.status(200).json({
        success: true,
        message: '啟動授權檢查通過。',
        installationId,
        storeName,
      })
    } catch (error) {
      sendError(response, error)
    }
  },
)

function validateReleaseFile(requestPath: string) {
  let relativePath: string
  try {
    relativePath = decodeURIComponent(requestPath).replace(/^\/+/, '')
  } catch {
    throw new ApiError(400, '更新檔案路徑格式不正確。')
  }

  if (!relativePath.startsWith('releases/')) throw new ApiError(404, '找不到更新檔案。')
  const fileName = relativePath.slice('releases/'.length)
  if (!fileName || fileName.includes('/') || fileName.includes('\\') || fileName.includes('..')) {
    throw new ApiError(400, '更新檔案名稱不正確。')
  }
  if (!(fileName === 'manifest.json' || /^SMSAutoSender-Setup-\d+\.\d+\.\d+\.exe$/.test(fileName))) {
    throw new ApiError(404, '不允許存取這個更新檔案。')
  }
  if (fileName.endsWith('.exe') && !fileName.startsWith(INSTALLER_PREFIX)) {
    throw new ApiError(404, '不允許存取這個更新檔案。')
  }
  return fileName
}

export const updateGateway = onRequest(
  { secrets: [activationHmacSecret] },
  async (request, response) => {
    if (handlePreflight(request, response)) return
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      response.status(405).json({ success: false, message: '只接受 GET 或 HEAD 請求。' })
      return
    }

    try {
      await requireActiveLicense(request)
      const fileName = validateReleaseFile(request.path)
      const file = getStorage().bucket().file(`releases/${fileName}`)
      const [exists] = await file.exists()
      if (!exists) throw new ApiError(404, '找不到指定的更新檔案。')

      const isEmulator = process.env.FUNCTIONS_EMULATOR === 'true'
      if (fileName.endsWith('.exe') && !isEmulator) {
        const [signedUrl] = await file.getSignedUrl({
          version: 'v4',
          action: 'read',
          expires: Date.now() + UPDATE_URL_TTL_MS,
        })
        response.set('Cache-Control', 'private, no-store')
        response.redirect(307, signedUrl)
        return
      }

      const [metadata] = await file.getMetadata()
      response.set('Content-Type', fileName === 'manifest.json' ? 'application/json; charset=utf-8' : 'application/octet-stream')
      response.set('Cache-Control', fileName === 'manifest.json' ? 'private, no-store' : 'private, max-age=300')
      response.set('Accept-Ranges', 'bytes')

      const totalSize = Number(metadata.size || 0)
      const rangeMatch = fileName.endsWith('.exe')
        ? /^bytes=(\d+)-(\d*)$/i.exec(request.header('range') || '')
        : null
      const rangeStart = rangeMatch ? Number(rangeMatch[1]) : 0
      const requestedEnd = rangeMatch?.[2] ? Number(rangeMatch[2]) : totalSize - 1
      const rangeEnd = Math.min(requestedEnd, totalSize - 1)
      if (rangeMatch && (rangeStart < 0 || rangeStart >= totalSize || rangeEnd < rangeStart)) {
        response.set('Content-Range', `bytes */${totalSize}`)
        response.status(416).send('')
        return
      }

      if (rangeMatch) {
        response.status(206)
        response.set('Content-Range', `bytes ${rangeStart}-${rangeEnd}/${totalSize}`)
        response.set('Content-Length', String(rangeEnd - rangeStart + 1))
      } else {
        response.set('Content-Length', String(totalSize))
      }
      if (request.method === 'HEAD') {
        response.status(200).send('')
        return
      }

      file.createReadStream(rangeMatch ? { start: rangeStart, end: rangeEnd } : undefined)
        .on('error', (error) => {
          console.error('Update metadata stream failed:', error)
          if (!response.headersSent) response.status(500).end()
          else response.destroy(error)
        })
        .pipe(response)
    } catch (error) {
      sendError(response, error)
    }
  },
)
