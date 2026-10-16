import SharedAssetPage from "../../../components/sharedAssetPage2019";
import { getProductInfoLegacy } from "../../../services/catalog";
import Head from "next/head";
import Theme2016 from "../../../components/theme2016";
const ItemPage = ({ name, description, assetId, ...props }) => {
  return(
    <>
      {name && (
        <Head>
          <title>{name} - Marine</title>
          <meta property="og:title" content={name} />
          <meta property="og:url" content={`https://www.silrev.biz/library/${assetId}/--`} />
          <meta property="og:type" content="profile" />
          <meta property="og:description" content={description} />
          <meta property="og:image" content={`https://www.silrev.biz/thumbs/asset.ashx?assetId=${assetId}`} />
          <meta name="twitter:card" content="summary_large_image" />
          <meta name="og:site_name" content="Marine" />
          <meta name="theme-color" content="#E2231A" />
        </Head>
      )}
      <SharedAssetPage idParamName='assetId' nameParamName='name' />
    </>
  );
}

export async function getServerSideProps(context) {
  const { assetId } = context.query;
  try {
    const info = await getProductInfoLegacy(assetId);
    return {
      props: {
        name: info?.Name || null,
        description: info?.Description || null,
        assetId: assetId || null
      }
    };
  } catch (error) {
    console.error('Failed to fetch library product info in getServerSideProps:', error.message);
    return {
      props: {
        name: null,
        description: null,
        assetId: assetId || null
      }
    };
  }
}
export default ItemPage;