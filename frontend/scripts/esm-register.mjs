// 注册上面的解析钩子：node --import ./scripts/esm-register.mjs <脚本>
import { register } from 'node:module'

register('./esm-resolve.mjs', import.meta.url)