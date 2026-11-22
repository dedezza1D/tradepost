/** Security headers on every answer of this route family (see edge/headers.ts). */
import { withSecurityHeaders } from '../../edge/headers'

export const onRequest: PagesFunction = async ({ next }) => withSecurityHeaders(await next())
