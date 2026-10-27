
<script setup>
import { ref, reactive, computed, onMounted } from 'vue'

// ==================== 基础配置 ====================

const API_URL = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

const CATEGORIES = [
  '餐饮', '交通', '购物', '居住', '娱乐',
  '教育', '医疗', '人情', '其他'
]

function today() {
  const now = new Date()
  const year = now.getFullYear()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function money(value) {
  return Number(value || 0).toFixed(2)
}

function errorMessage(data) {
  if (typeof data?.detail === 'string') return data.detail
  if (Array.isArray(data?.detail)) {
    return data.detail.map(item => item.msg).join('；')
  }
  return '请求失败'
}

// ==================== 用户认证 ====================

const token = ref(localStorage.getItem('accounting_token') || '')
const currentUser = ref(null)
const authMode = ref('login')
const authLoading = ref(false)
const authError = ref('')
const initializing = ref(true)

const authForm = reactive({
  email: '',
  password: '',
  confirmPassword: ''
})

function logout() {
  token.value = ''
  currentUser.value = null
  localStorage.removeItem('accounting_token')

  historyRecords.value = []
  totalRecords.value = 0
  statistics.value = emptyStatistics()
  chatRecords.value = []
  page.value = 1
  showRecordForm.value = false
}

async function apiRequest(path, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {})
  }

  if (token.value) {
    headers.Authorization = `Bearer ${token.value}`
  }

  let response

  try {
    response = await fetch(`${API_URL}${path}`, {
      ...options,
      headers
    })
  } catch {
    throw new Error('无法连接后端，请检查 FastAPI 是否正在运行')
  }

  let data
  try {
    data = await response.json()
  } catch {
    data = {}
  }

  if (!response.ok) {
    if (response.status === 401 && token.value) {
      logout()
      throw new Error('登录已失效，请重新登录')
    }
    throw new Error(errorMessage(data))
  }

  return data
}

async function loadUser() {
  if (!token.value) return

  currentUser.value = await apiRequest('/api/auth/me')
}

async function submitAuth() {
  if (authLoading.value) return
  authError.value = ''

  if (
    authMode.value === 'register' &&
    authForm.password !== authForm.confirmPassword
  ) {
    authError.value = '两次密码不一致'
    return
  }

  authLoading.value = true

  try {
    const credentials = {
      email: authForm.email.trim(),
      password: authForm.password
    }

    if (authMode.value === 'register') {
      await apiRequest('/api/auth/register', {
        method: 'POST',
        body: JSON.stringify(credentials)
      })
    }

    const result = await apiRequest('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify(credentials)
    })

    token.value = result.access_token
    localStorage.setItem('accounting_token', token.value)

    await loadUser()

    authForm.password = ''
    authForm.confirmPassword = ''

    resetFilters(false)
    await refreshData()
  } catch (error) {
    authError.value = error.message
  } finally {
    authLoading.value = false
  }
}

function changeAuthMode(mode) {
  authMode.value = mode
  authError.value = ''
  authForm.password = ''
  authForm.confirmPassword = ''
}

// ==================== 账单列表 ====================

const historyRecords = ref([])
const totalRecords = ref(0)
const page = ref(1)
const pageSize = 10
const recordLoading = ref(false)
const recordError = ref('')

const filters = reactive({
  start_date: '',
  end_date: '',
  category: '',
  record_type: ''
})

const totalPages = computed(() =>
  Math.max(1, Math.ceil(totalRecords.value / pageSize))
)

function buildQuery(params) {
  const query = new URLSearchParams()

  for (const [key, value] of Object.entries(params)) {
    if (value !== '' && value !== null && value !== undefined) {
      query.append(key, String(value))
    }
  }

  return query.toString()
}

function validDateRange() {
  if (
    filters.start_date &&
    filters.end_date &&
    filters.start_date > filters.end_date
  ) {
    recordError.value = '开始日期不能晚于结束日期'
    return false
  }
  return true
}

async function loadRecords() {
  if (!token.value || !currentUser.value) return

  recordLoading.value = true
  recordError.value = ''

  try {
    const query = buildQuery({
      start_date: filters.start_date,
      end_date: filters.end_date,
      category: filters.category,
      record_type: filters.record_type,
      page: page.value,
      page_size: pageSize
    })

    const result = await apiRequest(`/api/records?${query}`)
    historyRecords.value = result.items
    totalRecords.value = result.total
  } catch (error) {
    recordError.value = error.message
  } finally {
    recordLoading.value = false
  }
}

async function applyFilters() {
  recordError.value = ''
  if (!validDateRange()) return

  page.value = 1
  await refreshData()
}

function resetFilters(refresh = true) {
  Object.assign(filters, {
    start_date: '',
    end_date: '',
    category: '',
    record_type: ''
  })

  page.value = 1
  recordError.value = ''

  if (refresh) refreshData()
}

async function changePage(nextPage) {
  if (nextPage < 1 || nextPage > totalPages.value) return
  page.value = nextPage
  await loadRecords()
}

// ==================== 新增与修改 ====================

const showRecordForm = ref(false)
const editingId = ref(null)
const saving = ref(false)
const formError = ref('')

const recordForm = reactive({
  amount: '',
  type: '支出',
  category: '餐饮',
  record_date: today(),
  note: ''
})

function resetRecordForm() {
  editingId.value = null
  formError.value = ''

  Object.assign(recordForm, {
    amount: '',
    type: '支出',
    category: '餐饮',
    record_date: today(),
    note: ''
  })
}

function openAddForm() {
  resetRecordForm()
  showRecordForm.value = true
}

function openEditForm(record) {
  editingId.value = record.id
  formError.value = ''

  Object.assign(recordForm, {
    amount: record.amount,
    type: record.type,
    category: record.category,
    record_date: record.record_date,
    note: record.note || ''
  })

  showRecordForm.value = true
}

function closeRecordForm() {
  if (saving.value) return
  showRecordForm.value = false
  resetRecordForm()
}

async function saveRecord() {
  if (saving.value) return

  formError.value = ''

  const amount = Number(recordForm.amount)

  if (
    recordForm.amount === '' ||
    !Number.isFinite(amount) ||
    amount <= 0 ||
    !/^\d+(\.\d{1,2})?$/.test(String(recordForm.amount))
  ) {
    formError.value = '请输入大于 0 且最多保留两位小数的金额'
    return
  }

  if (!recordForm.record_date) {
    formError.value = '请选择发生日期'
    return
  }

  saving.value = true

  try {
    const payload = {
      amount,
      type: recordForm.type,
      category: recordForm.category,
      record_date: recordForm.record_date,
      note: recordForm.note
    }

    const isEditing = editingId.value !== null

    await apiRequest(
      isEditing ? `/api/records/${editingId.value}` : '/api/records',
      {
        method: isEditing ? 'PUT' : 'POST',
        body: JSON.stringify(payload)
      }
    )

    showRecordForm.value = false
    resetRecordForm()

    if (!isEditing) page.value = 1

    await refreshData()
  } catch (error) {
    formError.value = error.message
  } finally {
    saving.value = false
  }
}

// ==================== 删除账单 ====================

const deletingId = ref(null)

async function deleteRecord(record) {
  if (deletingId.value !== null) return

  const confirmed = window.confirm(
    `确定删除这笔账单吗？\n\n` +
    `日期：${record.record_date}\n` +
    `类型：${record.type}\n` +
    `分类：${record.category}\n` +
    `金额：¥${money(record.amount)}\n` +
    `备注：${record.note || '无'}\n\n` +
    '删除后无法恢复。'
  )

  if (!confirmed) return

  deletingId.value = record.id

  try {
    await apiRequest(`/api/records/${record.id}`, {
      method: 'DELETE'
    })

    if (historyRecords.value.length === 1 && page.value > 1) {
      page.value -= 1
    }

    await refreshData()
  } catch (error) {
    alert(error.message)
  } finally {
    deletingId.value = null
  }
}

// ==================== 统计 ====================

function emptyStatistics() {
  return {
    total_income: 0,
    total_expense: 0,
    balance: 0,
    record_count: 0,
    by_category: {},
    percentages: {}
  }
}

const statistics = ref(emptyStatistics())
const statisticsError = ref('')

async function loadStatistics() {
  if (!token.value || !currentUser.value) return

  statisticsError.value = ''

  try {
    const query = buildQuery({
      start_date: filters.start_date,
      end_date: filters.end_date
    })

    statistics.value = await apiRequest(`/api/statistics?${query}`)
  } catch (error) {
    statisticsError.value = error.message
  }
}

async function refreshData() {
  if (!currentUser.value) return

  await Promise.all([
    loadRecords(),
    loadStatistics()
  ])
}

// ==================== 聊天界面 ====================
// 通过后端调用 LangChain Agent

const chatInput = ref('')
const chatRecords = ref([])
const chatLoading = ref(false)
let chatId = 0

async function sendChat() {
  const text = chatInput.value.trim()
  if (!text || chatLoading.value) return

  chatLoading.value = true
  chatInput.value = ''

  chatRecords.value.push({
    id: ++chatId,
    role: 'user',
    text
  })

  try {
    const result = await apiRequest('/api/chat', {
      method: 'POST',
      body: JSON.stringify({ text })
    })

    chatRecords.value.push({
      id: ++chatId,
      role: 'assistant',
      text: result.reply
    })

    await refreshData()
  } catch (error) {
    chatRecords.value.push({
      id: ++chatId,
      role: 'assistant',
      text: error.message
    })
  } finally {
    chatLoading.value = false
  }
}

// ==================== 页面初始化 ====================

onMounted(async () => {
  try {
    if (token.value) {
      await loadUser()
      if (currentUser.value) {
        await refreshData()
      }
    }
  } catch (error) {
    console.error('初始化失败：', error)
  } finally {
    initializing.value = false
  }
})
</script>

<template>
  <div class="container">
    <header class="page-header">
      <h1>AI 智能记账助手</h1>
      <p class="subtitle">个人记账管理系统</p>
    </header>

    <p v-if="initializing" class="empty">正在加载...</p>

    <!-- 注册和登录 -->
    <section v-else-if="!currentUser" class="auth-card">
      <h2>{{ authMode === 'login' ? '用户登录' : '用户注册' }}</h2>

      <form @submit.prevent="submitAuth">
        <label>
          邮箱
          <input
            v-model="authForm.email"
            type="email"
            placeholder="请输入邮箱"
            autocomplete="email"
            required
          />
        </label>

        <label>
          密码
          <input
            v-model="authForm.password"
            type="password"
            placeholder="请输入密码"
            :autocomplete="authMode === 'login' ? 'current-password' : 'new-password'"
            required
          />
        </label>

        <template v-if="authMode === 'register'">
          <label>
            确认密码
            <input
              v-model="authForm.confirmPassword"
              type="password"
              placeholder="再次输入密码"
              autocomplete="new-password"
              required
            />
          </label>

          <p class="hint">
            密码至少 8 位，需包含大写字母、小写字母、数字和特殊字符。
          </p>
        </template>

        <p v-if="authError" class="error">{{ authError }}</p>

        <button type="submit" :disabled="authLoading">
          {{
            authLoading
              ? '处理中...'
              : authMode === 'login' ? '登录' : '注册'
          }}
        </button>
      </form>

      <p class="auth-switch">
        <template v-if="authMode === 'login'">
          没有账号？
          <button
            type="button"
            class="link-button"
            @click="changeAuthMode('register')"
          >
            立即注册
          </button>
        </template>

        <template v-else>
          已有账号？
          <button
            type="button"
            class="link-button"
            @click="changeAuthMode('login')"
          >
            返回登录
          </button>
        </template>
      </p>
    </section>

    <!-- 登录后页面 -->
    <template v-else>
      <div class="user-bar">
        <span>当前用户：{{ currentUser.email }}</span>
        <button class="secondary-button" @click="logout">
          退出登录
        </button>
      </div>

      <!-- 聊天 -->
      <section class="panel">
        <h2>💬 聊天记账</h2>

        <p class="hint">
          使用自然语言添加、查询和统计账单，也可以继续追问。
          聊天功能需要后端配置模型 API Key。
        </p>

        <div class="chat-box">
          <p v-if="chatRecords.length === 0" class="empty">
            试试输入：今天午饭花了 35 元，分类餐饮。
          </p>

          <div
            v-for="item in chatRecords"
            :key="item.id"
            class="chat-message"
            :class="item.role"
          >
            <strong>{{ item.role === 'user' ? '我：' : '助手：' }}</strong>
            {{ item.text }}
          </div>
        </div>

        <div class="chat-input">
          <input
            v-model="chatInput"
            placeholder="输入记账指令"
            :disabled="chatLoading"
            @keyup.enter="sendChat"
          />
          <button :disabled="chatLoading" @click="sendChat">
            {{ chatLoading ? '发送中...' : '发送' }}
          </button>
        </div>
      </section>

      <!-- 统计 -->
      <section class="panel">
        <h2>📊 收支统计</h2>

        <p class="hint">
          统计按照下方选择的日期范围计算。
        </p>

        <p v-if="statisticsError" class="error">
          {{ statisticsError }}
        </p>

        <div class="statistics-grid">
          <div class="stat-card">
            <span>总收入</span>
            <strong class="income">¥{{ money(statistics.total_income) }}</strong>
          </div>

          <div class="stat-card">
            <span>总支出</span>
            <strong class="expense">¥{{ money(statistics.total_expense) }}</strong>
          </div>

          <div class="stat-card">
            <span>结余</span>
            <strong>¥{{ money(statistics.balance) }}</strong>
          </div>

          <div class="stat-card">
            <span>记账笔数</span>
            <strong>{{ statistics.record_count }} 笔</strong>
          </div>
        </div>

        <h3>分类支出及占比</h3>

        <div
          v-for="category in CATEGORIES"
          :key="category"
          class="category-row"
        >
          <span>{{ category }}</span>
          <span>
            ¥{{ money(statistics.by_category[category]) }}
            （{{ money(statistics.percentages[category]) }}%）
          </span>
        </div>
      </section>

      <!-- 账单列表 -->
      <section class="panel">
        <div class="section-title">
          <h2>📒 记账列表</h2>
          <button @click="openAddForm">+ 新增账单</button>
        </div>

        <div class="filters">
          <label>
            开始日期
            <input v-model="filters.start_date" type="date" />
          </label>

          <label>
            结束日期
            <input v-model="filters.end_date" type="date" />
          </label>

          <label>
            分类
            <select v-model="filters.category">
              <option value="">全部分类</option>
              <option
                v-for="category in CATEGORIES"
                :key="category"
                :value="category"
              >
                {{ category }}
              </option>
            </select>
          </label>

          <label>
            收支类型
            <select v-model="filters.record_type">
              <option value="">全部</option>
              <option value="收入">收入</option>
              <option value="支出">支出</option>
            </select>
          </label>

          <button @click="applyFilters">查询</button>

          <button class="secondary-button" @click="resetFilters()">
            重置
          </button>
        </div>

        <p v-if="recordError" class="error">{{ recordError }}</p>

        <p v-if="recordLoading" class="hint">正在读取账单...</p>

        <div class="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>日期</th>
                <th>收支类型</th>
                <th>分类</th>
                <th>金额</th>
                <th>备注</th>
                <th>操作</th>
              </tr>
            </thead>

            <tbody>
              <tr v-for="record in historyRecords" :key="record.id">
                <td>{{ record.record_date }}</td>
                <td>{{ record.type }}</td>
                <td>{{ record.category }}</td>
                <td :class="record.type === '收入' ? 'income' : 'expense'">
                  ¥{{ money(record.amount) }}
                </td>
                <td>{{ record.note }}</td>
                <td>
                  <div class="actions">
                    <button
                      class="edit-button"
                      @click="openEditForm(record)"
                    >
                      编辑
                    </button>

                    <button
                      class="delete-button"
                      :disabled="deletingId !== null"
                      @click="deleteRecord(record)"
                    >
                      {{ deletingId === record.id ? '删除中...' : '删除' }}
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <p
          v-if="!recordLoading && historyRecords.length === 0 && !recordError"
          class="empty"
        >
          暂无符合条件的账单
        </p>

        <div class="pagination">
          <button
            :disabled="recordLoading || page <= 1"
            @click="changePage(page - 1)"
          >
            上一页
          </button>

          <span>
            第 {{ page }} / {{ totalPages }} 页
            （共 {{ totalRecords }} 条）
          </span>

          <button
            :disabled="recordLoading || page >= totalPages"
            @click="changePage(page + 1)"
          >
            下一页
          </button>
        </div>
      </section>

      <!-- 新增、编辑弹窗 -->
      <div
        v-if="showRecordForm"
        class="modal-overlay"
        @click.self="closeRecordForm"
      >
        <section class="modal">
          <h2>{{ editingId === null ? '新增账单' : '编辑账单' }}</h2>

          <form @submit.prevent="saveRecord">
            <label>
              金额
              <input
                v-model="recordForm.amount"
                type="number"
                min="0.01"
                max="9999999999.99"
                step="0.01"
                required
              />
            </label>

            <label>
              收支类型
              <select v-model="recordForm.type">
                <option value="支出">支出</option>
                <option value="收入">收入</option>
              </select>
            </label>

            <label>
              分类
              <select v-model="recordForm.category">
                <option
                  v-for="category in CATEGORIES"
                  :key="category"
                  :value="category"
                >
                  {{ category }}
                </option>
              </select>
            </label>

            <label>
              发生日期
              <input
                v-model="recordForm.record_date"
                type="date"
                required
              />
            </label>

            <label>
              备注
              <textarea
                v-model="recordForm.note"
                rows="3"
                maxlength="1000"
                placeholder="输入备注"
              ></textarea>
            </label>

            <p v-if="formError" class="error">{{ formError }}</p>

            <div class="form-actions">
              <button
                type="button"
                class="secondary-button"
                :disabled="saving"
                @click="closeRecordForm"
              >
                取消
              </button>

              <button type="submit" :disabled="saving">
                {{ saving ? '保存中...' : '保存' }}
              </button>
            </div>
          </form>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
* {
  box-sizing: border-box;
}

.container {
  max-width: 1100px;
  margin: 30px auto;
  padding: 20px;
  font-family: Arial, sans-serif;
  color: #333;
}

.page-header {
  text-align: center;
  margin-bottom: 28px;
}

h1 {
  margin-bottom: 8px;
}

h2 {
  font-size: 22px;
  margin: 0 0 18px;
}

h3 {
  margin-top: 24px;
}

.subtitle,
.hint {
  color: #888;
}

.hint {
  font-size: 13px;
  line-height: 1.6;
}

.panel,
.auth-card {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 12px;
  padding: 24px;
  margin-bottom: 24px;
}

.auth-card {
  max-width: 430px;
  margin: 40px auto;
}

form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

label {
  display: flex;
  flex-direction: column;
  gap: 7px;
  font-size: 14px;
}

input,
select,
textarea {
  width: 100%;
  padding: 10px;
  border: 1px solid #d5d9df;
  border-radius: 6px;
  font-size: 14px;
  font-family: inherit;
}

button {
  background: #409eff;
  color: white;
  border: none;
  border-radius: 6px;
  padding: 10px 16px;
  cursor: pointer;
  white-space: nowrap;
}

button:hover:not(:disabled) {
  opacity: 0.9;
}

button:disabled {
  background: #aaa;
  cursor: not-allowed;
}

.secondary-button {
  background: #909399;
}

.delete-button {
  background: #e74c3c;
}

.edit-button {
  background: #e6a23c;
}

.link-button {
  background: transparent;
  border: none;
  padding: 4px;
  color: #409eff;
  text-decoration: underline;
}

.link-button:hover {
  background: transparent;
}

.auth-switch {
  text-align: center;
  margin-top: 18px;
}

.user-bar {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  flex-wrap: wrap;
  gap: 15px;
  margin-bottom: 20px;
}

.error {
  color: #e74c3c;
  font-size: 14px;
}

.empty {
  color: #999;
  text-align: center;
  padding: 28px;
}

.chat-box {
  height: 220px;
  overflow-y: auto;
  border: 1px solid #ddd;
  border-radius: 8px;
  background: #fafafa;
  padding: 15px;
}

.chat-message {
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 10px;
  line-height: 1.6;
}

.chat-message.user {
  background: #e8f3ff;
}

.chat-message.assistant {
  background: #eaf8ea;
}

.chat-input {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}

.chat-input input {
  flex: 1;
  min-width: 0;
}

.statistics-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 15px;
}

.stat-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: #f5f7fa;
  border-radius: 8px;
  padding: 20px;
}

.stat-card span {
  color: #666;
}

.stat-card strong {
  font-size: 22px;
}

.income {
  color: #27ae60;
}

.expense {
  color: #e74c3c;
}

.category-row {
  display: flex;
  justify-content: space-between;
  gap: 15px;
  padding: 12px 0;
  border-bottom: 1px solid #eee;
}

.section-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
  margin: 20px 0;
}

.filters label {
  min-width: 125px;
  flex: 1;
}

.table-wrapper {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  padding: 12px;
  text-align: left;
  border-bottom: 1px solid #eee;
}

th {
  background: #f5f7fa;
}

.actions {
  display: flex;
  gap: 8px;
}

.pagination {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 15px;
  margin-top: 24px;
}

.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  z-index: 1000;
}

.modal {
  width: 100%;
  max-width: 460px;
  max-height: 90vh;
  overflow-y: auto;
  padding: 25px;
  background: white;
  border-radius: 12px;
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

@media (max-width: 700px) {
  .container {
    padding: 12px;
  }

  .statistics-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .actions {
    flex-direction: column;
  }

  .user-bar {
    justify-content: center;
  }
}
</style>
