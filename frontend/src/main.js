/**
 * 应用入口
 *
 * Element Plus 走全量引入而不是按需自动导入：本项目组件数量有限，全量引入
 * 换来的是不需要额外的 unplugin 配置与构建期转换，调试时堆栈也更直观。
 * 如果之后体积成为问题，再换 unplugin-vue-components 按需引入即可。
 */
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'

// 基础样式 → 深色模式变量 → 本项目的设计令牌（顺序不能反：
// 令牌要能覆盖 Element Plus 的默认变量）
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/theme.css'

import App from './App.vue'

createApp(App).use(ElementPlus, { locale: zhCn }).mount('#app')
