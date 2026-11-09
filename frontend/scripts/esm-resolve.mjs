// 让 Node 能直接跑源码：为 Vite 风格的无扩展名相对导入补 .js（仅用于本地检查脚本）。
export async function resolve(specifier, context, next) {
  try {
    return await next(specifier, context)
  } catch (error) {
    if (specifier.startsWith('.') && !/\.[a-z0-9]+$/i.test(specifier)) {
      return next(`${specifier}.js`, context)
    }
    throw error
  }
}