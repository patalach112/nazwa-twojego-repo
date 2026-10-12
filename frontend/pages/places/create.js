import CreateGame from "../../components/createGame";

const CreateGamePage = () => {
    return <CreateGame />
}

export const getServerSideProps = async () => {
    return {
        props: {
            title: 'Create Game - Marine',
        },
    };
};

export default CreateGamePage;