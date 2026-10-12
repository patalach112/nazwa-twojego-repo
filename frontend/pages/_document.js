import Document from 'next/document'
import React from 'react'
import {SheetsRegistry, JssProvider, createGenerateId} from 'react-jss'

export default class JssDocument extends Document {
    static async getInitialProps(ctx) {
        const registry = new SheetsRegistry()
        const generateId = createGenerateId()
        const originalRenderPage = ctx.renderPage
        ctx.renderPage = () =>
            originalRenderPage({
                enhanceApp: (App) => (props) => (
                    <JssProvider registry={registry} generateId={generateId}>
                        <App {...props} />
                    </JssProvider>
                ),
            })
        
        const initialProps = await Document.getInitialProps(ctx)
        
        return {
            ...initialProps,
            styles: (
                <>
                    {initialProps.styles}
                    <link href="/fonts/source-sans-pro.css" rel="stylesheet"/>
                    <link href="/fonts/gotham1.css" rel="stylesheet"/>
                    <style id="server-side-styles">{registry.toString()}</style>
                </>
            ),
        }
    }
}
